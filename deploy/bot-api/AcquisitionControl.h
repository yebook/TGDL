// Private per-Client acquisition ownership; never infer ownership from telemetry.
#pragma once
#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace telegram_bot_api {
class AcquisitionControl {
 public:
  enum class State { Prepared, Resolving, Downloading, Cancelling, Cancelled, Completed, Failed };
  struct Owner {
    std::string alias;
    State state = State::Prepared;
    int32_t file_id = 0;
    double touched = 0;
    bool settled = false;
    std::string expected_cache_identity;
  };
  struct File {
    uint64_t epoch = 0;
    unsigned starts = 0;
    bool stopping = false;
    bool stop_sent = false;
    std::vector<std::pair<std::string, State>> waiters;
  };
  static const char *name(State state) {
    switch (state) {
      case State::Prepared: return "prepared";
      case State::Resolving: return "resolving";
      case State::Downloading: return "downloading";
      case State::Cancelling: return "cancelling";
      case State::Cancelled: return "cancelled";
      case State::Completed: return "completed";
      case State::Failed: return "failed";
    }
    return "failed";
  }
  Owner *get(const std::string &id) {
    auto it = owners.find(id);
    return it == owners.end() ? nullptr : &it->second;
  }
  void prune(double now) {
    for (auto it = owners.begin(); it != owners.end();) {
      auto &owner = it->second;
      if (owner.state == State::Prepared && now - owner.touched >= 600) {
        // Keep authoritative no-start proof for a delayed application request.
        owner.state = State::Failed;
        owner.settled = true;
        owner.touched = now;
      }
      if (owner.settled && now - owner.touched >= 86400) it = owners.erase(it);
      else ++it;
    }
  }
  bool prepare(const std::string &id, const std::string &alias, double now) {
    prune(now);
    // Active/resolving/stopping records are never evicted to admit work.
    if (owners.size() >= 4096) return false;
    return owners.emplace(id, Owner{alias, State::Prepared, 0, now, false, {}}).second;
  }
  bool claim(const std::string &id, const std::string &alias, double now) {
    prune(now);
    auto *owner = get(id);
    if (!owner || owner->state != State::Prepared || owner->alias != alias) return false;
    owner->state = State::Resolving;
    owner->touched = now;
    return true;
  }
  bool busy() const {
    for (const auto &item : owners) if (!item.second.settled) return true;
    for (const auto &item : files) if (item.second.stopping || item.second.starts) return true;
    return false;
  }
  bool blocks(int32_t file_id) const {
    auto it = files.find(file_id);
    return it != files.end() && it->second.stopping;
  }
  uint64_t start(int32_t file_id) {
    auto &file = files[file_id];
    if (!file.epoch) file.epoch = ++next_epoch;
    ++file.starts;
    return file.epoch;
  }
  std::map<std::string, Owner> owners;
  std::map<int32_t, File> files;
 private:
  uint64_t next_epoch = 0;
};
}  // namespace telegram_bot_api
