"""Offline real TDLib registration/delete/restart fixture; never production.

Only authorization/network, synthetic completed-file setup and deterministic
fault/timing hooks change. The recovery parser/hash/registration/unlink are real.
"""
from pathlib import Path


def replace(name: str, old: str, new: str) -> None:
    path = Path('/src') / name
    source = path.read_text()
    if source.count(old) != 1:
        raise ValueError(f'Pinned recovery fixture drift: {name}: {old[:80]}')
    path.write_text(source.replace(old, new))


replace('telegram-bot-api/Client.cpp',
        'make_object<td_api::networkTypeOther>()', 'make_object<td_api::networkTypeNone>()')
replace('telegram-bot-api/Client.cpp', '''    case td_api::authorizationStateWaitPhoneNumber::ID:
      send_request(make_object<td_api::setOption>("online", make_object<td_api::optionValueBoolean>(true)),
                   td::make_unique<TdOnOkCallback>());
      return send_request(make_object<td_api::checkAuthenticationBotToken>(bot_token_),
                          td::make_unique<TdOnAuthorizationCallback>(this));''', '''    case td_api::authorizationStateWaitPhoneNumber::ID:
      was_authorized_ = true;
      return loop();''')
replace('telegram-bot-api/Client.cpp', 'void Client::send(PromisedQueryPtr query) {', '''void Client::send(PromisedQueryPtr query) {
  if (query->method() == "tgdfixturedelayedsend") {
    // Exercise the secondary barrier as if command parsing had already queued
    // a native send before maintenance began. Null defaults never reach TDLib.
    return send_request(make_object<td_api::sendMessage>(),
                        td::make_unique<TdOnOkQueryCallback>(std::move(query)));
  }
  if (query->method() == "tgdfixtureseed" || query->method() == "tgdfixturelookup") {
    class FixtureCallback final : public TdQueryCallback {
     public:
      FixtureCallback(PromisedQueryPtr query, bool lookup) : query_(std::move(query)), lookup_(lookup) {}
      void on_result(object_ptr<td_api::Object> result) final {
        if (lookup_ && result->get_id() == td_api::file::ID) {
          auto file = move_object_as<td_api::file>(result);
          return answer_query(td::JsonString(file->local_->path_), std::move(query_));
        }
        if (!lookup_ && result->get_id() == td_api::testString::ID) {
          auto value = move_object_as<td_api::testString>(result);
          return answer_query(td::JsonString(value->value_), std::move(query_));
        }
        fail_query(500, "Internal Server Error: fixture unavailable", std::move(query_));
      }
     private:
      PromisedQueryPtr query_;
      bool lookup_;
    };
    if (query->method() == "tgdfixturelookup") {
      auto alias = query->arg("file_id").str();
      return send_request(make_object<td_api::getRemoteFile>(alias, nullptr),
                          td::make_unique<FixtureCallback>(std::move(query), true));
    }
    auto setup = "recovery-seed|" + query->arg("serial").str() + "|" + query->arg("path").str();
    return send_request(make_object<td_api::testCallString>(setup),
                        td::make_unique<FixtureCallback>(std::move(query), false));
  }''')

replace('td/td/telegram/Requests.cpp', '#include "td/telegram/Requests.h"',
        '#include "td/telegram/Requests.h"\n#include "td/telegram/files/FileLocation.hpp"')
replace('td/td/telegram/Requests.cpp',
        'void Requests::on_request(uint64 id, td_api::testCallString &request) {', '''void Requests::on_request(uint64 id, td_api::testCallString &request) {
  if (begins_with(request.x_, "recovery-seed|")) {
    auto rest = request.x_.substr(14);
    auto split = rest.find('|');
    CHECK(split != string::npos);
    auto serial = to_integer<int64>(rest.substr(0, split));
    auto path = rest.substr(split + 1);
    FullRemoteFileLocation remote(FileType::Video, 0x54474452454300 + serial, 12345,
                                  DcId::internal(2), "synthetic-reference");
    auto remote_id = td_->file_manager_->register_remote(remote, FileLocationSource::FromServer, DialogId(), 0, 0, "fixture.mp4");
    auto local = td_->file_manager_->register_local(FullLocalFileLocation(FileType::Video, path, 0), DialogId(), 0, false, true, remote_id);
    CHECK(local.is_ok());
    auto alias = td_->file_manager_->get_file_object(local.ok())->remote_->id_;
    send_closure(td_actor_, &Td::send_result, id, td_api::make_object<td_api::testString>(alias));
    return;
  }''')
path = Path('/src/td/td/telegram/Td.cpp')
source = path.read_text()
anchor = '    case td_api::getStorageStatistics::ID:'
start = source.index('bool Td::is_preauthentication_request(')
end = source.index('\n}', start)
fragment = source[start:end]
for method in ('getRemoteFile', 'getFile', 'deleteFile', 'tgdRecoverFileCache', 'tgdCancelDownloadFile'):
    case = f'    case td_api::{method}::ID:'
    if case not in fragment:
        fragment = fragment.replace(anchor, case + '\n' + anchor)
path.write_text(source[:start] + fragment + source[end:])

# Yielding holds preserve Client responsiveness while a real proof is open.
replace('td/td/telegram/files/FileLoadManager.cpp', '#include <cerrno>',
        '#include <cerrno>\n#include <fstream>\n#include <thread>\n#include <chrono>')
replace('td/td/telegram/files/FileLoadManager.cpp', '  void loop() final {\n    auto count = ::read', '''  void loop() final {
    if (std::ifstream("/tmp/tgd-recovery-hash.hold").good()) {
      std::ofstream("/tmp/tgd-recovery-hash.entered") << "opened";
      set_timeout_in(0.02);
      return;
    }
    auto count = ::read''')
replace('td/td/telegram/files/FileLoadManager.cpp', '  void tear_down() final { if (hash_)',
        '  void timeout_expired() final { yield(); }\n  void tear_down() final { if (hash_)')
replace('td/td/telegram/files/FileLoadManager.cpp',
        'void FileLoadManager::tgd_unlink_file(string file_path, Promise<Unit> promise, std::shared_ptr<TgdCacheProof> proof) {', '''void FileLoadManager::tgd_unlink_file(string file_path, Promise<Unit> promise, std::shared_ptr<TgdCacheProof> proof) {
  if (std::ifstream("/tmp/tgd-recovery-unlink.fail").good()) {
    // Match the pinned production swallowed unlink error after metadata drop.
    promise.set_value(Unit());
    return;
  }''')
replace('td/td/telegram/files/FileLoadManager.cpp',
        '  if (proof && (proof->path != file_path || !proof->matches()))', '''  if (proof && std::ifstream("/tmp/tgd-recovery-unlink.hold").good()) {
    std::ofstream("/tmp/tgd-recovery-unlink.entered") << "registered-and-dropped";
    for (int i = 0; i < 1000 && std::ifstream("/tmp/tgd-recovery-unlink.hold").good(); ++i)
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }
  if (proof && (proof->path != file_path || !proof->matches()))''')
replace('td/td/telegram/files/FileManager.cpp', '#include "td/telegram/files/FileManager.h"',
        '#include "td/telegram/files/FileManager.h"\n#include <fstream>\n#include <thread>\n#include <chrono>')
replace('td/td/telegram/files/FileManager.cpp', '  if (found != local_location_to_file_id_.end() && found->second.is_valid()) {',
        '''  if (found != local_location_to_file_id_.end() && found->second.is_valid()) {
    std::ofstream("/tmp/tgd-recovery-reused-id") << found->second.get();''')
replace('td/td/telegram/files/FileManager.cpp', '  if (!had_registration) recovery_id = id;', '''  if (!had_registration) recovery_id = id;
  if (std::ifstream("/tmp/tgd-recovery-registration.hold").good()) {
    std::ofstream("/tmp/tgd-recovery-registration.entered") << "registered";
    for (int i = 0; i < 1000 && std::ifstream("/tmp/tgd-recovery-registration.hold").good(); ++i)
      std::this_thread::sleep_for(std::chrono::milliseconds(5));
  }''')
