#pragma once

#include <cerrno>
#include <cstdint>
#include <string>
#include <sys/stat.h>
#include <unordered_set>

namespace telegram_bot_api {

class CacheCleanupGate {
 public:
  bool begin_resolution() {
    if (resolving_ || !deleting_.empty()) return false;
    resolving_ = true;
    return true;
  }
  void resolved() { resolving_ = false; }
  void begin_delete(std::int32_t id) { deleting_.insert(id); }
  void completed(std::int32_t id) { deleting_.erase(id); }
  bool blocks(std::int32_t id) const { return resolving_ || deleting_.count(id) != 0; }
 private:
  bool resolving_ = false;
  std::unordered_set<std::int32_t> deleting_;
};

inline bool cache_file_busy(bool downloading, bool uploading, bool listeners, bool download_started) {
  return downloading || uploading || listeners || download_started;
}

// Observation only: unlink and cache metadata changes belong exclusively to TDLib.
inline bool cache_path_absent(const std::string &path) {
  struct stat info;
  return lstat(path.c_str(), &info) != 0 && errno == ENOENT;
}

inline bool cache_file_matches(const std::string &actual_path, const std::string &expected_path,
                               std::int64_t actual_size, std::int64_t expected_size,
                               bool completed, bool deletable) {
  struct stat info;
  return expected_size >= 0 && actual_path == expected_path && actual_size == expected_size &&
         completed && deletable && lstat(actual_path.c_str(), &info) == 0 &&
         S_ISREG(info.st_mode) && info.st_size == expected_size;
}

}  // namespace telegram_bot_api
