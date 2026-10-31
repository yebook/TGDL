"""Prepare an isolated native HTTP fixture; never included in production artifacts.

Retain production URL/form parsing, Query normalization, connection dispatch and
HTTP actors. Only scale the original idle timer and replace the upstream
handler with a local delayed answer. No Telegram client is created by a query.
"""
from __future__ import annotations

import argparse
from pathlib import Path

FIXTURE = r'''
// Isolated verifier only: local delayed handler, never part of a shipped binary.
class HttpTimeoutFixture final : public td::Actor {
 public:
  explicit HttpTimeoutFixture(PromisedQueryPtr query) : query_(std::move(query)) {}
 private:
  PromisedQueryPtr query_;
  void start_up() final {
    auto value = query_->arg("fixture_delay");
    set_timeout_in(value.empty() ? 0.01 : std::stod(value.str()));
  }
  void timeout_expired() final {
    if (query_->arg("fixture_error") == "1") {
      query_->set_error(500, td::BufferSlice("{}"));
    } else if (query_->arg("fixture_large") == "1") {
      query_->set_ok(td::BufferSlice(td::string(16 << 20, 'x')));
    } else {
      query_->set_ok(td::BufferSlice("{}"));
    }
    query_.reset();
    stop();
  }
};
'''


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text()
    if text.count(old) != 1:
        raise ValueError(f"Pinned fixture source changed: {path.name}")
    path.write_text(text.replace(old, new))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", action="store_true")
    parser.add_argument("--previous-cap", action="store_true")
    args = parser.parse_args()
    connection = Path("/src/telegram-bot-api/HttpConnection.cpp")
    timer = "send_closure(connection_, &td::HttpInboundConnection::cancel_timeout);"
    previous = "send_closure(connection_, &td::HttpInboundConnection::set_timeout_in, 4.0);"
    if args.previous_cap:
        # Reproduce the superseded 7200s cap at a short, controlled deadline.
        replace_once(connection, timer, previous)
        return
    if args.baseline:
        # The same fixture and native actors with only the fix removed.
        replace_once(connection, previous, "/* Original pending-response deadline. */")
        return
    if connection.read_text().count(timer) != 1:
        raise ValueError("Pinned pending-response cancellation changed")
    replace_once(Path("/src/telegram-bot-api/HttpServer.h"), "IDLE_TIMEOUT = 500;", "IDLE_TIMEOUT = 1;")
    replace_once(connection, "namespace telegram_bot_api {", "namespace telegram_bot_api {\n" + FIXTURE)
    replace_once(connection,
                 "send_closure(client_manager_, &ClientManager::send, std::move(promised_query));",
                 'td::create_actor<HttpTimeoutFixture>("HttpTimeoutFixture", std::move(promised_query)).release();')


if __name__ == "__main__":
    main()
