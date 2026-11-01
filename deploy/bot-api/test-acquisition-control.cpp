#include "AcquisitionControl.h"
#include <cassert>
#include <iostream>
using telegram_bot_api::AcquisitionControl;
int main() {
  using State = AcquisitionControl::State;
  AcquisitionControl state;
  assert(state.prepare("a", "alias", 1));
  assert(!state.claim("unknown", "alias", 1));
  assert(!state.claim("a", "different-alias", 1));
  assert(state.claim("a", "alias", 1));
  assert(!state.claim("a", "alias", 1));  // one request per owner
  state.prune(1000000);
  assert(state.get("a"));  // active owners never expire
  assert(state.prepare("idle", "alias", 1000000));
  state.prune(1000600);
  assert(state.get("idle")->settled && state.get("idle")->state == State::Failed);
  assert(!state.claim("idle", "alias", 1000600));  // delayed request cannot resurrect
  auto *owner = state.get("a");
  owner->state = State::Cancelled;
  owner->settled = true;
  owner->touched = 1000600;
  assert(!state.claim("a", "alias", 1000600));
  state.prune(1087000);
  assert(!state.get("a"));
  assert(!state.busy());
  for (int i = 0; i < 4096; ++i) {
    auto id = std::to_string(i);
    assert(state.prepare(id, "alias", 1087000));
    assert(state.claim(id, "alias", 1087000));
  }
  assert(!state.prepare("overflow", "alias", 1087000));
  state.prune(999999999);
  assert(state.owners.size() == 4096);  // admission fails closed; no active eviction
  assert(state.busy());
  auto epoch = state.start(42);
  assert(state.files[42].starts == 1);
  assert(state.start(42) == epoch);
  state.files.erase(42);
  assert(state.start(42) != epoch);  // old callback cannot settle a new file attempt
  std::cout << "Acquisition ownership, expiry, capacity and epoch fences passed\n";
}
