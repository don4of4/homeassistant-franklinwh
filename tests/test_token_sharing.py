"""Two gateways on one account must share a login.

richo/homeassistant-franklinwh#87: a fresh login invalidates the account's
previous token, so two clients each refreshing on their own knock each other
out - A logs in, B gets 401 and logs in (killing A's token), A gets 401 and
logs in again, and so on. Sharing one TokenFetcher and adopting its newer
token stops the loop.
"""

from conftest import run

from custom_components.franklin_wh import api
from custom_components.franklin_wh.api import client as client_mod


class CountingFetcher(api.TokenFetcher):
    def __init__(self):
        super().__init__("user@example.com", "secret")
        self.logins = 0

    async def fetch_token(self):
        self.logins += 1
        return {"token": f"T{self.logins}"}


def bare_client(fetcher, gateway):
    c = client_mod.Client.__new__(client_mod.Client)
    c.fetcher = fetcher
    c.gateway = gateway
    c.url_base = api.DEFAULT_URL_BASE
    c.token = ""
    return c


def test_second_client_adopts_the_shared_token_without_logging_in():
    fetcher = CountingFetcher()
    a, b = bare_client(fetcher, "GW-A"), bare_client(fetcher, "GW-B")
    run(a.refresh_token())
    run(b.refresh_token())
    assert fetcher.logins == 1, "B must reuse A's login, not make its own"
    assert a.token == b.token == "T1"


def test_a_client_holding_the_current_token_logs_in_again_and_peers_follow():
    """Only the client whose token is the newest one can prove it is dead."""
    fetcher = CountingFetcher()
    a, b = bare_client(fetcher, "GW-A"), bare_client(fetcher, "GW-B")
    run(a.refresh_token())
    run(b.refresh_token())
    # The API rejected T1 (e.g. the phone app logged in); A refreshes first.
    run(a.refresh_token())
    assert fetcher.logins == 2 and a.token == "T2"
    # B's next 401 must adopt T2, not log in a third time and kill T2.
    run(b.refresh_token())
    assert fetcher.logins == 2 and b.token == "T2"


def test_single_client_behaviour_unchanged():
    fetcher = CountingFetcher()
    a = bare_client(fetcher, "GW-A")
    run(a.refresh_token())
    run(a.refresh_token())
    assert fetcher.logins == 2, "a lone client still gets a fresh login per refresh"
