#pragma once

#include <cerrno>
#include <dirent.h>
#include <fcntl.h>
#include <memory>
#include <string>
#include <sys/stat.h>
#include <unistd.h>
#include <vector>

namespace telegram_bot_api {

// Pinned TDLib FileType.cpp names. Bot API enables
// store_all_files_in_files_directory, so common/secure media share files_dir_.
inline const std::vector<std::string> &cache_media_directories() {
  static const std::vector<std::string> names = {
      "thumbnails", "profile_photos", "photos", "voice", "videos", "documents", "secret", "temp",
      "stickers", "music", "animations", "secret_thumbnails", "video_notes", "passport", "wallpapers",
      "notification_sounds", "stories"};
  return names;
}

inline bool cache_media_directory(const std::string &name) {
  for (const auto &item : cache_media_directories()) if (item == name) return true;
  return false;
}

// Retain every ancestor from /, rejecting links and rechecking the complete
// pathname-to-descriptor binding immediately before each empty-only rmdir.
class CacheDirectoryChain {
 public:
  CacheDirectoryChain() = default;
  CacheDirectoryChain(const CacheDirectoryChain &) = delete;
  CacheDirectoryChain &operator=(const CacheDirectoryChain &) = delete;
  ~CacheDirectoryChain() { while (!nodes_.empty()) pop(); }

  bool root(const std::string &path) {
    if (!nodes_.empty() || path.empty() || path[0] != '/') return false;
    int fd = ::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (fd < 0) return false;
    nodes_.push_back({fd, ""});
    std::size_t start = 1;
    while (start < path.size()) {
      auto end = path.find('/', start);
      if (end == std::string::npos) end = path.size();
      if (end == start || !push(path.substr(start, end - start))) return false;
      start = end + 1;
    }
    root_depth_ = nodes_.size();
    return root_depth_ > 1 && unchanged();
  }
  bool push(const std::string &name) {
    if (nodes_.empty() || name.empty() || name == "." || name == ".." ||
        name.find('/') != std::string::npos || name.find('\0') != std::string::npos) return false;
    int child = ::openat(fd(), name.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (child < 0) return false;
    nodes_.push_back({child, name});
    if (unchanged()) return true;
    pop();
    return false;
  }
  bool unchanged() const {
    for (std::size_t i = 1; i < nodes_.size(); ++i) {
      struct stat opened, named;
      if (::fstat(nodes_[i].fd, &opened) != 0 ||
          ::fstatat(nodes_[i - 1].fd, nodes_[i].name.c_str(), &named, AT_SYMLINK_NOFOLLOW) != 0 ||
          !S_ISDIR(opened.st_mode) || !S_ISDIR(named.st_mode) ||
          opened.st_dev != named.st_dev || opened.st_ino != named.st_ino) return false;
    }
    return !nodes_.empty();
  }
  bool remove_empty() {
    if (nodes_.size() <= root_depth_ || !unchanged()) return false;
    return ::unlinkat(nodes_[nodes_.size() - 2].fd, nodes_.back().name.c_str(), AT_REMOVEDIR) == 0;
  }
  int fd() const { return nodes_.back().fd; }
  void pop() { ::close(nodes_.back().fd); nodes_.pop_back(); }
  void prune_parents() {
    while (nodes_.size() > root_depth_) {
      if (!remove_empty()) return;
      pop();
    }
  }

 private:
  struct Node { int fd; std::string name; };
  std::vector<Node> nodes_;
  std::size_t root_depth_ = 0;
};

// Single-file cleanup only prunes its proved-absent parent chain; never scans
// sibling trees or changes the already established file-deletion outcome.
inline std::shared_ptr<CacheDirectoryChain> prepare_cache_file_directories(std::string root,
                                                                         const std::string &path) {
  while (root.size() > 1 && root.back() == '/') root.pop_back();
  if (path.compare(0, root.size() + 1, root + "/") != 0) return nullptr;
  auto parent_end = path.rfind('/');
  if (parent_end <= root.size()) return nullptr;
  auto chain = std::make_shared<CacheDirectoryChain>();
  if (!chain->root(root)) return nullptr;
  std::size_t start = root.size() + 1, depth = 0;
  while (start < parent_end) {
    auto end = path.find('/', start);
    if (end == std::string::npos || end > parent_end) return nullptr;
    auto name = path.substr(start, end - start);
    if ((depth == 0 && !cache_media_directory(name)) || depth == 64 || !chain->push(name)) return nullptr;
    ++depth;
    start = end + 1;
  }
  return chain;
}

inline void prune_cache_directory_tree(CacheDirectoryChain &chain, std::size_t depth, std::size_t &budget) {
  if (depth > 64 || budget == 0 || !chain.unchanged()) return;
  --budget;
  // openat(".") creates a separate stream offset from the retained identity FD.
  int scan_fd = ::openat(chain.fd(), ".", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
  if (scan_fd < 0) return;
  auto *directory = ::fdopendir(scan_fd);
  if (!directory) { ::close(scan_fd); return; }
  while (budget > 0) {
    auto *entry = ::readdir(directory);
    if (!entry) break;
    std::string name(entry->d_name);
    if (name == "." || name == "..") continue;
    --budget;
    if (chain.push(name)) {
      prune_cache_directory_tree(chain, depth + 1, budget);
      chain.pop();
    }
  }
  ::closedir(directory);
  // This is the only mutation. A new entry, inaccessible directory or race
  // makes rmdir fail safely. It never removes files or follows directory links.
  chain.remove_empty();
}

inline void prune_cache_empty_directories(const std::string &root) {
  CacheDirectoryChain chain;
  if (!chain.root(root)) return;
  std::size_t budget = 8192;
  for (const auto &name : cache_media_directories()) {
    if (budget == 0) break;
    if (chain.push(name)) {
      prune_cache_directory_tree(chain, 0, budget);
      chain.pop();
    }
  }
}

}  // namespace telegram_bot_api
