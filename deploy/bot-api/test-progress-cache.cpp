#include "ProgressCache.h"
#include <cassert>
#include <iostream>

using telegram_bot_api::ProgressCache;

int main() {
  ProgressCache a, b;
  assert(a.find("unknown", 0) == nullptr);
  a.bind("alias1", 1, {200, 1000, 1100, true, false}, 0);
  a.bind("alias2", 1, {200, 1000, 1100, true, false}, 0);
  assert(b.find("alias1", 0) == nullptr);  // Per-Bot cache isolation.
  a.update(1, {600, 1000, 1200, true, false});
  assert(a.find("alias1", 1)->downloaded == 600);
  assert(a.find("alias2", 1)->size == 1000);
  a.update(1, {1000, 1000, 1200, false, true});
  a.finish(1, 2, false);
  assert(a.find("alias2", 61.99)->completed);
  assert(!a.find("alias1", 62));
  assert(!a.find("alias2", 62));
  a.bind("estimated", 2, {0, 0, 1500, false, false}, 63);
  assert(a.find("estimated", 64)->expected == 1500);
  a.update(2, {150, 0, 0, true, false});
  assert(a.find("estimated", 64)->expected == 0);
  a.finish(2, 65, true);
  assert(!a.find("estimated", 65));
  // A retry rebinding a terminal entry cancels old retention expiry.
  a.bind("retry", 3, {1, 5, 0, true, false}, 70);
  a.finish(3, 71, false);
  a.bind("retry", 3, {2, 5, 0, true, false}, 72);
  assert(a.find("retry", 200)->downloaded == 2);
  for (int i = 4; i < 131; ++i) a.bind(std::to_string(i), i, {}, 201);
  a.bind("overflow", 999, {}, 201);
  assert(!a.find("overflow", 201));
  ProgressCache aliases;
  for (int i = 0; i < 1025; ++i) aliases.bind(std::to_string(i), 1, {}, 0);
  assert(aliases.find("1023", 0));
  assert(!aliases.find("1024", 0));
  assert(!ProgressCache().find("retry", 201));  // A new child has no old snapshots.
  std::cout << "production_progress_cache: passed\n";
}
