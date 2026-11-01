#pragma once

// Private TDLib cache recovery proof. Never unlinks a file; deletion remains
// FileManager::delete_file -> FileLoadManager. Linux/amd64 native build only.
#include <fcntl.h>
#include <sys/stat.h>
#include <unistd.h>
#include <memory>
#include <string>
#include <vector>

namespace td {

class TgdCacheProof final {
 public:
  ~TgdCacheProof() {
    if (fd >= 0) ::close(fd);
    if (parent >= 0) ::close(parent);
  }
  int fd = -1;
  int parent = -1;
  std::string root, path, name;
  struct stat identity{}, directory{};

  static int open_directory(const std::string &path) {
    if (path.empty() || path[0] != '/') return -1;
    int current = ::open("/", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (current < 0) return -1;
    std::size_t at = 1;
    while (at < path.size()) {
      auto end = path.find('/', at);
      if (end == std::string::npos) end = path.size();
      auto part = path.substr(at, end - at);
      if (part.empty() || part == "." || part == "..") { ::close(current); return -1; }
      int next = ::openat(current, part.c_str(), O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
      ::close(current);
      if (next < 0) return -1;
      current = next;
      at = end + 1;
    }
    return current;
  }
  static bool same(const struct stat &a, const struct stat &b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_mode == b.st_mode &&
           a.st_size == b.st_size && a.st_nlink == b.st_nlink &&
           a.st_mtim.tv_sec == b.st_mtim.tv_sec && a.st_mtim.tv_nsec == b.st_mtim.tv_nsec &&
           a.st_ctim.tv_sec == b.st_ctim.tv_sec && a.st_ctim.tv_nsec == b.st_ctim.tv_nsec;
  }
  static std::shared_ptr<TgdCacheProof> open(const std::string &root, const std::string &path, long long size) {
    if (root.empty() || root.back() != '/' || path.compare(0, root.size(), root) != 0) return {};
    auto name = path.substr(root.size());
    if (name.empty() || name == "." || name == ".." || name.find('/') != std::string::npos ||
        name.find('\0') != std::string::npos) return {};
    auto proof = std::make_shared<TgdCacheProof>();
    proof->root = root;
    proof->path = path;
    proof->name = name;
    proof->parent = open_directory(root);
    if (proof->parent < 0 || ::fstat(proof->parent, &proof->directory)) return {};
    proof->fd = ::openat(proof->parent, name.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
    if (proof->fd < 0 || ::fstat(proof->fd, &proof->identity) || !S_ISREG(proof->identity.st_mode) ||
        proof->identity.st_nlink != 1 || proof->identity.st_size != size || !proof->matches()) return {};
    return proof;
  }
  bool matches() const {
    struct stat opened{}, named{}, dir{};
    int fresh = open_directory(root);
    if (fresh < 0) return false;
    bool valid = ::fstat(fresh, &dir) == 0 && dir.st_dev == directory.st_dev && dir.st_ino == directory.st_ino &&
                 ::fstatat(fresh, name.c_str(), &named, AT_SYMLINK_NOFOLLOW) == 0 &&
                 ::fstat(fd, &opened) == 0 && same(identity, opened) && same(identity, named);
    ::close(fresh);
    return valid;
  }
};

}  // namespace td
