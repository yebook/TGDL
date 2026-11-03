"""Disposable actor unlink latch and OS-full fixture; no production behavior changes."""
from pathlib import Path

path = Path('/src/td/td/telegram/files/FileLoadManager.cpp')
source = path.read_text()
source = source.replace('#include <cerrno>', '#include <cerrno>\n#include <fstream>\n#include <thread>\n#include <chrono>')
anchor = '  if (proof && (proof->path != file_path || !proof->matches()))'
assert source.count(anchor) == 1
source = source.replace(anchor, '''  if (std::ifstream("/tmp/tgd-partial-delete.hold").good()) {
    std::ofstream("/tmp/tgd-partial-delete.entered") << "held";
    for (int i = 0; i < 1500 && std::ifstream("/tmp/tgd-partial-delete.hold").good(); ++i)
      std::this_thread::sleep_for(std::chrono::milliseconds(10));
    CHECK(!std::ifstream("/tmp/tgd-partial-delete.hold").good());
  }
''' + anchor)
path.write_text(source)

# Genuine FileManager OS error classification, without filling a host filesystem.
path = Path('/src/td/td/telegram/files/FileDownloader.cpp')
source = path.read_text()
anchor = '        TRY_RESULT(written, fd_.pwrite(bytes, part.offset));'
assert source.count(anchor) == 1
source = source.replace(anchor, '''        if (std::ifstream("/tmp/tgd-download-stop.enospc").good())
          return Status::PosixError(ENOSPC, "synthetic offline write");
''' + anchor)
path.write_text(source)
