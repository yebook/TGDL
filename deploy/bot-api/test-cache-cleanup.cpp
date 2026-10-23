#include "CacheCleanup.h"

#include <cassert>
#include <cstdlib>
#include <fstream>
#include <unistd.h>

int main() {
  using namespace telegram_bot_api;
  CacheCleanupGate gate;
  assert(gate.begin_resolution());
  assert(gate.blocks(1) && gate.blocks(2));
  assert(!gate.begin_resolution());
  gate.resolved();
  gate.begin_delete(1);
  assert(gate.blocks(1));  // All aliases resolving to native ID 1 are excluded.
  assert(!gate.blocks(2));
  assert(!gate.begin_resolution());
  // HTTP timeout does not touch this gate. Only the native callback completes it.
  assert(gate.blocks(1));
  gate.completed(1);
  assert(!gate.blocks(1));
  assert(gate.begin_resolution());
  gate.resolved();
  assert(!cache_file_busy(false, false, false, false));
  for (int bit = 0; bit < 4; ++bit) {
    assert(cache_file_busy(bit == 0, bit == 1, bit == 2, bit == 3));
  }
  char directory[] = "/tmp/tgd-cache-policy-XXXXXX";
  assert(mkdtemp(directory) != nullptr);
  const std::string path = std::string(directory) + "/media";
  assert(cache_path_absent(path));
  { std::ofstream file(path); file << "12345"; }
  assert(!cache_path_absent(path));
  assert(cache_file_matches(path, path, 5, 5, true, true));
  assert(!cache_file_matches(path, path + "other", 5, 5, true, true));
  assert(!cache_file_matches(path, path, 6, 5, true, true));
  assert(!cache_file_matches(path, path, 5, 6, true, true));
  assert(!cache_file_matches(path, path, 5, 5, false, true));
  assert(!cache_file_matches(path, path, 5, 5, true, false));
  assert(!cache_file_matches(directory, directory, 5, 5, true, true));
  assert(!cache_path_absent(path + "/not-a-directory"));
  const auto link = path + ".link";
  assert(symlink(path.c_str(), link.c_str()) == 0);
  assert(!cache_file_matches(link, link, 5, 5, true, true));
  assert(unlink(path.c_str()) == 0);
  assert(cache_path_absent(path));
  assert(!cache_path_absent(link));  // Dangling symlink is still a retained entry.
  assert(unlink(link.c_str()) == 0);
  assert(rmdir(directory) == 0);
}
