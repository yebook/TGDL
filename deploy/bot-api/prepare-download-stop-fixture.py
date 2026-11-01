"""Build-only real TDLib writer fixture; never copied into production sources.

Keep FileManager, FileDownloadManager, actor cancellation, callback destruction,
FileDownloader partial-file initialization and descriptor lifetime intact. Replace
only the synthetic remote's network part delivery with deterministic local bytes
and delay its real tear_down behind an isolated filesystem latch.
"""

from pathlib import Path

ROOT = Path('/src/td')


def replace(name: str, old: str, new: str) -> None:
    path = ROOT / name
    value = path.read_text()
    if value.count(old) != 1:
        raise ValueError(f'Pinned download-stop fixture source drift: {name}')
    path.write_text(value.replace(old, new))


replace('td/telegram/Requests.cpp', '#include "td/telegram/Requests.h"',
        '''#include "td/telegram/Requests.h"
#include "td/telegram/files/FileLocation.hpp"
#include "td/telegram/Version.h"
#include "td/utils/base64.h"
#include "td/utils/misc.h"
#include "td/utils/tl_helpers.h"''')
replace('td/telegram/Requests.cpp',
        'void Requests::on_request(uint64 id, td_api::testCallString &request) {',
        '''void Requests::on_request(uint64 id, td_api::testCallString &request) {
  if (request.x_ == "tgd-stop-fixture" || request.x_ == "tgd-stop-fixture-alias") {
    const bool alternate = request.x_ == "tgd-stop-fixture-alias";
    FullRemoteFileLocation location(FileType::Video, 0x54474453544f50,
                                    alternate ? 67890 : 12345, DcId::internal(2),
                                    alternate ? "synthetic-reference-alias" : "synthetic-reference");
    auto file_id = td_->file_manager_->register_remote(
        location,
        FileLocationSource::FromServer, DialogId(), 8 * 1024 * 1024,
        8 * 1024 * 1024, "tgd-stop-fixture.bin");
    auto alias = td_->file_manager_->get_file_object(file_id)->remote_->id_;
    if (alternate) {
      // Exact pinned FileNode::get_persistent_id wire format; this real public
      // alias varies reference/hash while the remote identity maps to one node.
      auto binary = zero_encode(serialize(location));
      binary.push_back(static_cast<char>(narrow_cast<uint8>(Version::Next) - 1));
      binary.push_back(4);
      alias = base64url_encode(binary);
    }
    send_closure(td_actor_, &Td::send_result, id, td_api::make_object<td_api::testString>(
        std::move(alias)));
    return;
  }''')
replace('td/telegram/files/FileDownloader.cpp', '#include <tuple>',
        '#include <tuple>\n#include <thread>\n#include <chrono>\n#include <fstream>\n#include <mutex>\n#include <map>')
replace('td/telegram/files/FileDownloader.cpp', 'namespace td {',
        '''namespace td {
namespace {
std::mutex tgd_fixture_mutex;
std::map<const void *, uint64> tgd_fixture_writers;
uint64 tgd_fixture_next_serial = 0;
uint64 tgd_fixture_first(const void *writer) {
  std::lock_guard<std::mutex> lock(tgd_fixture_mutex);
  if (tgd_fixture_writers.count(writer)) {
    return 0;
  }
  const auto serial = ++tgd_fixture_next_serial;
  tgd_fixture_writers.emplace(writer, serial);
  return serial;
}
bool tgd_fixture_live(const void *writer, uint64 serial) {
  std::lock_guard<std::mutex> lock(tgd_fixture_mutex);
  auto it = tgd_fixture_writers.find(writer);
  return it != tgd_fixture_writers.end() && it->second == serial;
}
bool tgd_fixture_erase(const void *writer) {
  std::lock_guard<std::mutex> lock(tgd_fixture_mutex);
  return tgd_fixture_writers.erase(writer) != 0;
}
// No FileDownloader header edit: avoid rebuilding every FileManager consumer.
// This pulse owns no downloader or descriptor, and uses the existing public
// update method to wake its genuine loop while synthetic network data is idle.
class TgdFixturePulse final : public Actor {
 public:
  TgdFixturePulse(ActorId<FileDownloader> writer, const void *identity, uint64 serial)
      : writer_(std::move(writer)), identity_(identity), serial_(serial) {
  }
 private:
  ActorId<FileDownloader> writer_;
  const void *identity_;
  uint64 serial_;
  void start_up() final {
    set_timeout_in(0.02);
  }
  void timeout_expired() final {
    if (!tgd_fixture_live(identity_, serial_)) {
      return stop();
    }
    send_closure(writer_, &FileDownloader::update_downloaded_part, 0, 0, 8 * 1024 * 1024);
    set_timeout_in(0.02);
  }
};
}  // namespace
#define TGD_FIXTURE_DOWNLOAD (remote_.is_common() && remote_.get_id() == 0x54474453544f50)
''')
replace('td/telegram/files/FileDownloader.cpp',
        'void FileDownloader::try_release_fd() {',
        '''void FileDownloader::try_release_fd() {
  if (TGD_FIXTURE_DOWNLOAD) {
    return;  // Keep the genuine writer descriptor open until actor destruction.
  }''')
replace('td/telegram/files/FileDownloader.cpp',
        'Status FileDownloader::do_loop() {',
        '''Status FileDownloader::do_loop() {
  if (TGD_FIXTURE_DOWNLOAD) {
    const bool finish = std::ifstream("/tmp/tgd-download-stop.complete").good();
    const auto first = tgd_fixture_first(this);
    if (first) {
      create_actor<TgdFixturePulse>("TgdFixturePulse", actor_id(this), this, first).release();
    }
    if (first || finish) {
      do {
        TRY_RESULT(part, parts_manager_.start_part());
        if (part.size == 0) {
          break;
        }
        TRY_STATUS(acquire_fd());
        string bytes(part.size, static_cast<char>('A' + part.id));
        TRY_RESULT(written, fd_.pwrite(bytes, part.offset));
        CHECK(written == bytes.size());
        TRY_STATUS(parts_manager_.on_part_ok(part.id, part.size, written));
        callback_->on_start_download();
        on_progress();  // Real partial location/bitmask flows back to FileManager.
        std::ofstream("/tmp/tgd-download-stop.path") << path_;
        std::ofstream("/tmp/tgd-download-stop.parts", std::ios::app) << part.id << "\\n";
      } while (finish && !parts_manager_.may_finish());
    }
    if (!finish || !parts_manager_.may_finish()) {
      return Status::OK();  // Only synthetic network delivery is replaced.
    }
    // Fall through to the unchanged real finish/close/rename/on_ok sequence.
  }''')
replace('td/telegram/files/FileDownloader.cpp',
        '    callback_->on_ok(FullLocalFileLocation(remote_.file_type_, std::move(path), 0), size, !only_check_);',
        '''    if (TGD_FIXTURE_DOWNLOAD) {
      std::ofstream("/tmp/tgd-download-stop.path") << path;
    }
    callback_->on_ok(FullLocalFileLocation(remote_.file_type_, std::move(path), 0), size, !only_check_);''')
replace('td/telegram/files/FileDownloader.cpp',
        'void FileDownloader::tear_down() {',
        '''void FileDownloader::tear_down() {
  if (TGD_FIXTURE_DOWNLOAD) {
    const bool wrote = tgd_fixture_erase(this);
    CHECK(!wrote || !fd_.empty() || stop_flag_);
    std::ofstream("/tmp/tgd-download-stop.entered") << (fd_.empty() ? "writer-closed" : "writer-open");
    // Bound a failed fixture; no production source contains this latch or delay.
    for (int i = 0; i < 1500 && std::ifstream("/tmp/tgd-download-stop.hold").good(); ++i) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
    CHECK(!std::ifstream("/tmp/tgd-download-stop.hold").good());
  }''')
