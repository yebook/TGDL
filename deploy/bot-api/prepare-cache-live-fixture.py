"""Build-only latch around real stats traversal; never copied to production."""
from pathlib import Path

source = Path('/src/td/td/telegram/files/FileStatsWorker.cpp')
text = source.read_text()
anchor = '  for (int32 i = 0; i < MAX_FILE_TYPE; i++) {'
if text.count(anchor) != 1 or text.count('#include <cerrno>') != 1:
    raise ValueError('Pinned cache live fixture source drift')
text = text.replace('#include <cerrno>', '#include <fstream>\n#include <cerrno>')
entry_anchor = '    auto name = string(entry->d_name);'
if text.count(entry_anchor) != 1:
    raise ValueError('Pinned cache live directory traversal drift')
text = text.replace(entry_anchor, entry_anchor + '''
    // Force a normal temp->final rename between readdir and fstatat. The scan
    // must continue through the remaining siblings even without usable d_type.
    if (name == "vanish-during-scan") {
      std::string destination;
      if (std::getline(std::ifstream("/tmp/tgd-cache-race.destination"), destination)) {
        ::rename((path + "/" + name).c_str(), destination.c_str());
        std::ofstream("/tmp/tgd-cache-race.entered") << "renamed";
      }
    }''')
text = text.replace(anchor, '''  if (std::ifstream("/tmp/tgd-cache-scan.hold").good()) {
    std::ofstream("/tmp/tgd-cache-scan.entered") << "temp-scanned";
    auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(5);
    while (std::ifstream("/tmp/tgd-cache-scan.hold").good() && std::chrono::steady_clock::now() < deadline) {
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }
  }
''' + anchor)
source.write_text(text)

source = Path('/src/telegram-bot-api/Client.cpp')
text = source.read_text()
anchor = 'void Client::send(PromisedQueryPtr query) {'
if text.count(anchor) != 1:
    raise ValueError('Pinned cache live Client fixture source drift')
text = text.replace(anchor, anchor + '''
  if (query->method() == "tgdfixtureacquisitionbusy") {
    return answer_query(td::JsonLong(tgd_acquisitions_.busy() ? 1 : 0), std::move(query));
  }''')
source.write_text(text)
