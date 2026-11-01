#pragma once

#include <algorithm>
#include <cstdint>
#include <string>
#include <unordered_set>

namespace telegram_bot_api {

// Owned by the Client actor, independently of HTTP request lifetimes.
class CacheStorage {
 public:
  enum class Scan { Idle, Running, Ready, Failed };
  enum class Operation { None, Running, Succeeded, Failed };
  enum class Admission { Accepted, Repeated, Busy };

  Scan scan = Scan::Idle;
  Operation operation = Operation::None;
  bool has_usage = false;
  std::int64_t used_bytes = 0;
  std::int64_t file_count = 0;
  std::string operation_id;
  bool has_before = false;
  bool has_after = false;
  std::int64_t before_bytes = 0;
  std::int64_t after_bytes = 0;
  std::int64_t released_bytes = 0;
  const char *error_code = nullptr;

  bool busy() const { return scan == Scan::Running || operation == Operation::Running; }
  // Read-only scans exclude GC/deletion, never media acquisition or controls.
  bool mutation_busy() const { return operation == Operation::Running; }
  bool begin_scan() {
    if (busy()) return false;
    scan = Scan::Running;
    return true;
  }
  Admission begin_operation(const std::string &id, bool native_busy) {
    if (operation != Operation::None && id == operation_id) return Admission::Repeated;
    if (busy() || native_busy) return Admission::Busy;
    operation_id = id;
    operation = Operation::Running;
    scan = Scan::Running;
    has_before = has_after = false;
    error_code = nullptr;
    return Admission::Accepted;
  }
  void scanned(std::int64_t bytes, std::int64_t count) {
    has_usage = true;
    used_bytes = bytes;
    file_count = count;
    scan = Scan::Ready;
  }
  void before_scanned(std::int64_t bytes, std::int64_t count) {
    scanned(bytes, count);
    has_before = true;
    before_bytes = bytes;
  }
  void succeeded(std::int64_t bytes, std::int64_t count) {
    scanned(bytes, count);
    has_after = true;
    after_bytes = bytes;
    released_bytes = std::max<std::int64_t>(before_bytes - after_bytes, 0);
    operation = Operation::Succeeded;
  }
  void failed(bool mutation_may_be_running) {
    // Do not expose an old snapshot as the result of an incomplete scan.
    has_usage = false;
    scan = Scan::Failed;
    if (operation == Operation::Running) {
      error_code = "CACHE_CLEANUP_FAILED";
      if (!mutation_may_be_running) operation = Operation::Failed;
    }
  }
  void observe_file(std::int32_t id, bool downloading, bool uploading) {
    if (!downloading && !uploading) {
      active_files_.erase(id);
    } else if (active_files_.size() < 4096) {
      active_files_.insert(id);
    } else {
      // Unknown activity remains protected until this Client is replaced.
      activity_overflow_ = true;
    }
  }
  bool has_active_files() const { return activity_overflow_ || !active_files_.empty(); }
  static bool valid_operation_id(const std::string &id) {
    if (id.size() != 32) return false;
    for (char c : id) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
  }

 private:
  std::unordered_set<std::int32_t> active_files_;
  bool activity_overflow_ = false;
};

}  // namespace telegram_bot_api
