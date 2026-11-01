"""Build-only offline Client authorization for real downloader cancellation tests.

The production parser, acquisition controller and TDLib stop barrier are intact.
No substitutions in this file enter the production binary or bundled artifact.
The companion TDLib fixture provides a synthetic remote and real writer actor.
"""
from pathlib import Path


def replace(relative: str, old: str, new: str) -> None:
    path = Path('/src') / relative
    source = path.read_text()
    if source.count(old) != 1:
        raise ValueError(f'Pinned acquisition fixture source drift: {relative}')
    path.write_text(source.replace(old, new))


replace('telegram-bot-api/Client.cpp',
        'make_object<td_api::networkTypeOther>()', 'make_object<td_api::networkTypeNone>()')
replace('telegram-bot-api/Client.cpp', '#include "telegram-bot-api/Client.h"', '#include "telegram-bot-api/Client.h"\n#include <fstream>')
replace('telegram-bot-api/Client.h', '  AcquisitionControl tgd_acquisitions_;', '''  AcquisitionControl tgd_acquisitions_;
  struct TgdFixtureResolver {
    td::string id;
    PromisedQueryPtr query;
    object_ptr<td_api::Object> result;
  };
  struct TgdFixtureStart {
    int32 file_id;
    td::uint64 epoch;
    object_ptr<td_api::Object> result;
  };
  td::vector<TgdFixtureResolver> tgd_fixture_resolvers_;
  td::vector<TgdFixtureStart> tgd_fixture_starts_;''')
replace('telegram-bot-api/Client.cpp', '''    case td_api::authorizationStateWaitPhoneNumber::ID:
      send_request(make_object<td_api::setOption>("online", make_object<td_api::optionValueBoolean>(true)),
                   td::make_unique<TdOnOkCallback>());
      return send_request(make_object<td_api::checkAuthenticationBotToken>(bot_token_),
                          td::make_unique<TdOnAuthorizationCallback>(this));''', '''    case td_api::authorizationStateWaitPhoneNumber::ID:
      // Test-only existing Client. Never authenticate or enable a network.
      was_authorized_ = true;
      return loop();''')
replace('telegram-bot-api/Client.cpp', 'void Client::send(PromisedQueryPtr query) {', '''void Client::send(PromisedQueryPtr query) {
  if (query->method() == "tgdfixturelisteners") {
    int64 count = 0;
    for (const auto &entry : file_download_listeners_) count += entry.second.size();
    return answer_query(td::JsonLong(count), std::move(query));
  }
  if (query->method() == "tgdfixturereleaseresolvers") {
    auto pending = std::move(tgd_fixture_resolvers_);
    tgd_fixture_resolvers_.clear();
    for (auto &value : pending) {
      tgd_acquisition_resolved(std::move(value.id), std::move(value.query), std::move(value.result));
    }
    return answer_query(td::JsonTrue(), std::move(query));
  }
  if (query->method() == "tgdfixturereleasestarts") {
    auto pending = std::move(tgd_fixture_starts_);
    tgd_fixture_starts_.clear();
    for (auto &value : pending) {
      tgd_acquisition_started(value.file_id, value.epoch, std::move(value.result));
    }
    return answer_query(td::JsonTrue(), std::move(query));
  }
  if (query->method() == "tgdfixturefile") {
    class FixtureCallback final : public TdQueryCallback {
     public:
      explicit FixtureCallback(PromisedQueryPtr query) : query_(std::move(query)) {}
      void on_result(object_ptr<td_api::Object> result) final {
        if (result->get_id() != td_api::testString::ID) {
          return fail_query(500, "Internal Server Error: fixture unavailable", std::move(query_));
        }
        auto value = move_object_as<td_api::testString>(result);
        answer_query(td::JsonString(value->value_), std::move(query_));
      }
     private:
      PromisedQueryPtr query_;
    };
    auto name = query->arg("variant") == "alias" ? "tgd-stop-fixture-alias" : "tgd-stop-fixture";
    return send_request(make_object<td_api::testCallString>(name),
                        td::make_unique<FixtureCallback>(std::move(query)));
  }''')
replace('telegram-bot-api/Client.cpp',
        'void Client::tgd_acquisition_resolved(td::string id, PromisedQueryPtr query, object_ptr<td_api::Object> result) {',
        '''void Client::tgd_acquisition_resolved(td::string id, PromisedQueryPtr query, object_ptr<td_api::Object> result) {
  if (std::ifstream("/tmp/tgd-acquisition-resolve.hold").good()) {
    tgd_fixture_resolvers_.push_back({std::move(id), std::move(query), std::move(result)});
    std::ofstream("/tmp/tgd-acquisition-resolve.entered") << "held";
    return;
  }''')
replace('telegram-bot-api/Client.cpp',
        'void Client::tgd_acquisition_started(int32 file_id, td::uint64 epoch, object_ptr<td_api::Object> result) {',
        '''void Client::tgd_acquisition_started(int32 file_id, td::uint64 epoch, object_ptr<td_api::Object> result) {
  if (std::ifstream("/tmp/tgd-acquisition-start.hold").good()) {
    tgd_fixture_starts_.push_back({file_id, epoch, std::move(result)});
    std::ofstream("/tmp/tgd-acquisition-start.entered") << "held";
    return;
  }''')

# The real writer fixture runs before authentication and never enables a route.
# These methods operate only on the synthetic remote registered by testCallString.
path = Path('/src/td/td/telegram/Td.cpp')
source = path.read_text()
anchor = '    case td_api::getStorageStatistics::ID:'
if source.count(anchor) != 1:
    raise ValueError('Pinned acquisition fixture preauth drift')
start = source.index('bool Td::is_preauthentication_request(')
end = source.index('\n}', start)
preauth = source[start:end]
for method in ('getRemoteFile', 'getFile', 'downloadFile', 'tgdCancelDownloadFile'):
    case = f'    case td_api::{method}::ID:'
    if case not in preauth:
        preauth = preauth.replace(anchor, case + '\n' + anchor)
source = source[:start] + preauth + source[end:]
path.write_text(source)

# Keep a read-only scan observably in flight while getFile and cancellation
# continue on the real Client actor; no production timing is changed.
replace('td/td/telegram/files/FileStatsWorker.cpp',
        '#include <cerrno>', '#include <thread>\n#include <chrono>\n#include <cerrno>')
replace('td/td/telegram/files/FileStatsWorker.cpp',
        '  Status scan_error = Status::OK();',
        '  std::this_thread::sleep_for(std::chrono::milliseconds(500));\n  Status scan_error = Status::OK();')
