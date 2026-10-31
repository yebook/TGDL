"""Build-only offline fixture; none of these substitutions enter production.

Real Bot API parsing/Client state and real TDLib storage workers remain intact.
Only authorization/network enablement and deterministic timing are substituted.
"""
from pathlib import Path


def replace(path: str, old: str, new: str) -> None:
    source = Path('/src') / path
    value = source.read_text()
    if value.count(old) != 1:
        raise ValueError(f'Pinned offline fixture source drift: {path}')
    source.write_text(value.replace(old, new))


replace('telegram-bot-api/Client.cpp',
        'make_object<td_api::networkTypeOther>()', 'make_object<td_api::networkTypeNone>()')
replace('telegram-bot-api/Client.cpp', '''    case td_api::authorizationStateWaitPhoneNumber::ID:
      send_request(make_object<td_api::setOption>("online", make_object<td_api::optionValueBoolean>(true)),
                   td::make_unique<TdOnOkCallback>());
      return send_request(make_object<td_api::checkAuthenticationBotToken>(bot_token_),
                          td::make_unique<TdOnAuthorizationCallback>(this));''', '''    case td_api::authorizationStateWaitPhoneNumber::ID:
      // Test-only existing Client; never authenticate or enable a network.
      was_authorized_ = true;
      return loop();''')
# optimizeStorage normally requires authorization. This single fixture-only
# preauth exception lets synthetic media exercise its genuine storage workers.
replace('td/td/telegram/Td.cpp', '    case td_api::getStorageStatistics::ID:',
        '    case td_api::optimizeStorage::ID:\n    case td_api::getStorageStatistics::ID:')
replace('td/td/telegram/files/FileStatsWorker.cpp',
        '#include <cerrno>', '#include <thread>\n#include <chrono>\n#include <cerrno>')
replace('td/td/telegram/files/FileStatsWorker.cpp',
        '  Status scan_error = Status::OK();',
        '  std::this_thread::sleep_for(std::chrono::milliseconds(150));\n  Status scan_error = Status::OK();')
replace('td/td/telegram/files/FileGcWorker.cpp',
        '#include <algorithm>', '#include <thread>\n#include <chrono>\n#include <algorithm>')
replace('td/td/telegram/files/FileGcWorker.cpp', '  auto begin_time = Time::now();',
        '  std::this_thread::sleep_for(std::chrono::milliseconds(400));\n  auto begin_time = Time::now();')
replace('telegram-bot-api/Client.cpp', 'void Client::send(PromisedQueryPtr query) {', '''void Client::send(PromisedQueryPtr query) {
  // Test-only native activity injection, never emitted by production.
  if (query->method() == "tgdfixtureactivity") {
    auto kind = query->arg("kind");
    tgd_cache_storage_.observe_file(999999, kind == "download", kind == "upload");
    return answer_query(td::JsonTrue(), std::move(query));
  }''')
