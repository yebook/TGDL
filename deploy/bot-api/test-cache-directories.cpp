#include "CacheDirectories.h"

#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>

namespace fs = std::filesystem;
using namespace telegram_bot_api;

void prune_cache_file_directories(const std::string &root, const std::string &path) {
  auto chain = prepare_cache_file_directories(root, path);
  if (chain) chain->prune_parents();
}

int main() {
  char pattern[] = "/tmp/tgd-empty-directories-XXXXXX";
  auto *temporary = ::mkdtemp(pattern);
  assert(temporary && ::getuid() != 0);
  fs::path base(temporary), bot = base / "bot";
  fs::create_directories(bot / "videos" / "old" / "nested");
  fs::create_directories(bot / "auth" / "empty");
  fs::create_directories(base / "other-bot" / "videos");
  std::ofstream(bot / "td.binlog") << "runtime";
  std::ofstream(bot / "videos" / "keep") << "media";
  prune_cache_file_directories(bot.string(), (bot / "videos" / "old" / "nested" / "gone").string());
  assert(!fs::exists(bot / "videos" / "old"));
  assert(fs::exists(bot / "videos" / "keep"));
  prune_cache_file_directories(bot.string(), (bot / "auth" / "empty" / "gone").string());
  prune_cache_file_directories(bot.string(), (base / "other-bot" / "videos" / "gone").string());
  assert(fs::is_directory(bot / "auth" / "empty") && fs::is_directory(base / "other-bot" / "videos"));
  fs::create_directories(bot / "temp" / "old" / "empty");
  fs::create_directories(bot / "photos" / "occupied");
  std::ofstream(bot / "photos" / "occupied" / "new-entry") << "preserve";
  fs::create_directory_symlink(base / "other-bot" / "videos", bot / "temp" / "link");
  prune_cache_empty_directories(bot.string());
  assert(!fs::exists(bot / "temp" / "old"));
  assert(fs::is_symlink(bot / "temp" / "link"));
  assert(fs::is_directory(base / "other-bot" / "videos"));
  assert(fs::exists(bot / "photos" / "occupied" / "new-entry"));
  assert(fs::exists(bot / "td.binlog") && fs::is_directory(bot / "auth" / "empty"));

  // A replacement after no-follow opening is never removed, even if empty.
  fs::create_directories(bot / "videos" / "replacement");
  {
    auto admitted = prepare_cache_file_directories(bot.string(), (bot / "videos" / "replacement" / "gone").string());
    assert(admitted);
    fs::rename(bot / "videos" / "replacement", bot / "videos" / "admitted-original");
    fs::create_directory(bot / "videos" / "replacement");
    admitted->prune_parents();
    assert(fs::is_directory(bot / "videos" / "replacement"));
  }
  {
    CacheDirectoryChain chain;
    assert(chain.root(bot.string()) && chain.push("videos") && chain.push("replacement"));
    fs::rename(bot / "videos" / "replacement", bot / "videos" / "original");
    fs::create_directory(bot / "videos" / "replacement");
    assert(!chain.remove_empty());
    assert(fs::is_directory(bot / "videos" / "replacement"));
  }
  {
    CacheDirectoryChain chain;
    assert(chain.root(bot.string()) && chain.push("videos") && chain.push("replacement"));
    std::ofstream(bot / "videos" / "replacement" / "arrived") << "new";
    assert(!chain.remove_empty());
  }
  {
    CacheDirectoryChain chain;
    assert(chain.root(bot.string()) && chain.push("videos"));
    fs::rename(bot, base / "renamed-bot");
    fs::create_directories(bot / "videos");
    assert(!chain.remove_empty());
    assert(fs::is_directory(bot / "videos"));
    fs::remove_all(bot);  // Synthetic test-owned setup only.
    fs::rename(base / "renamed-bot", bot);
  }
  fs::create_directories(bot / "music" / "denied");
  ::chmod((bot / "music").c_str(), 0500);
  prune_cache_empty_directories(bot.string());
  assert(fs::is_directory(bot / "music" / "denied"));
  ::chmod((bot / "music").c_str(), 0700);
  prune_cache_empty_directories(bot.string());
  assert(!fs::exists(bot / "music"));
  assert(fs::is_directory(bot));
  fs::create_directory_symlink(bot, base / "linked-bot");
  prune_cache_empty_directories((base / "linked-bot").string());
  assert(fs::is_directory(bot / "auth" / "empty"));
  fs::remove_all(base);  // Only this mkdtemp tree; no mounted/business data.
  std::cout << "PASS: exact media scope, parent chains, nonempty/new entries, replacements, symlinks and permissions\n";
}
