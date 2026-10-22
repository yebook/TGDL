#pragma once

// Actor-owned, bounded observations only. No filesystem or Telegram operations.
#include <algorithm>
#include <cstdint>
#include <map>
#include <string>

namespace telegram_bot_api {
class ProgressCache {
 public:
  struct Snapshot {
    std::int64_t downloaded = 0, size = 0, expected = 0;
    bool active = false, completed = false;
    double expires = 0;
  };
  void prune(double now) {
    for (auto it = files_.begin(); it != files_.end();) {
      if (it->second.expires > 0 && it->second.expires <= now) {
        auto id = it->first;
        it = files_.erase(it);
        for (auto a = aliases_.begin(); a != aliases_.end();) {
          if (a->second == id) a = aliases_.erase(a); else ++a;
        }
      } else ++it;
    }
  }
  void bind(const std::string &alias, int id, Snapshot snapshot, double now) {
    prune(now);
    if (alias.empty() || alias.size() > 4096) return;
    // Saturation makes new observations unavailable; it never changes downloads.
    if ((!files_.count(id) && files_.size() >= 128) ||
        (!aliases_.count(alias) && aliases_.size() >= 1024)) return;
    snapshot.expires = 0;
    files_[id] = snapshot;
    aliases_[alias] = id;
  }
  void update(int id, Snapshot snapshot) {
    auto it = files_.find(id);
    if (it == files_.end()) return;
    snapshot.expires = it->second.expires;
    it->second = snapshot;
  }
  void finish(int id, double now, bool failed) {
    auto it = files_.find(id);
    if (it == files_.end()) return;
    if (failed) {
      it->second.expires = now;
      prune(now);
    } else it->second.expires = now + 60;
  }
  const Snapshot *find(const std::string &alias, double now) {
    prune(now);
    auto a = aliases_.find(alias);
    if (a == aliases_.end()) return nullptr;
    auto f = files_.find(a->second);
    return f == files_.end() ? nullptr : &f->second;
  }
 private:
  std::map<int, Snapshot> files_;
  std::map<std::string, int> aliases_;
};
}  // namespace telegram_bot_api
