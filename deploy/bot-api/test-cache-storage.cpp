#include "CacheStorage.h"

#include <cassert>

int main() {
  using namespace telegram_bot_api;
  using Admission = CacheStorage::Admission;
  using Operation = CacheStorage::Operation;
  const std::string first(32, 'a'), second(32, 'b');
  CacheStorage state;
  assert(!state.has_usage && !state.busy());
  assert(state.begin_scan());
  assert(state.busy() && !state.begin_scan());
  assert(!state.mutation_busy());  // Scanning does not fence download work.
  assert(state.begin_operation(first, false) == Admission::Busy);
  state.scanned(0, 2);  // Empty/sparse files still count.
  assert(state.has_usage && state.file_count == 2);
  assert(state.begin_operation(first, true) == Admission::Busy);
  assert(state.operation == Operation::None);  // Rejected IDs were never accepted.
  assert(state.begin_operation(first, false) == Admission::Accepted);
  assert(state.mutation_busy());
  assert(!state.begin_scan());
  assert(state.begin_operation(first, true) == Admission::Repeated);
  assert(state.begin_operation(second, false) == Admission::Busy);
  state.before_scanned(8192, 4);
  state.succeeded(4096, 2);
  assert(state.released_bytes == 4096 && state.has_after && !state.busy());
  assert(!state.mutation_busy());
  assert(state.begin_operation(first, false) == Admission::Repeated);
  assert(state.operation == Operation::Succeeded);  // Terminal retry cannot repeat GC.
  assert(state.begin_operation(second, false) == Admission::Accepted);
  state.before_scanned(1, 1);
  state.succeeded(2, 2);
  assert(state.released_bytes == 0);  // Never fabricate negative released space.
  assert(state.begin_operation(first, false) == Admission::Accepted);
  state.failed(false);
  assert(!state.has_usage && !state.busy() && !state.has_after);
  assert(state.operation == Operation::Failed);
  assert(state.begin_operation(second, false) == Admission::Accepted);
  state.before_scanned(123, 1);
  state.failed(true);  // HTTP/native cancellation cannot release an uncertain GC.
  assert(state.busy() && !state.has_usage && !state.has_after);
  assert(state.error_code != nullptr && state.operation == Operation::Running);
  assert(state.begin_operation(second, false) == Admission::Repeated);
  assert(state.begin_operation(first, false) == Admission::Busy);
  assert(!state.begin_scan());

  CacheStorage activity;
  activity.observe_file(1, true, false);
  assert(activity.has_active_files());
  activity.observe_file(1, false, true);
  assert(activity.has_active_files());
  activity.observe_file(1, false, false);
  assert(!activity.has_active_files());
  for (int id = 0; id < 4097; ++id) activity.observe_file(id, false, true);
  for (int id = 0; id < 4097; ++id) activity.observe_file(id, false, false);
  assert(activity.has_active_files());  // Overflow stays fail closed.
  assert(CacheStorage::valid_operation_id(first));
  assert(!CacheStorage::valid_operation_id(std::string(32, 'A')));
  assert(!CacheStorage::valid_operation_id(std::string(31, 'a')));
  assert(!CacheStorage::valid_operation_id(std::string(32, 'g')));
}
