"""The contract key is called ``symbol`` on the wire, not ``occ_symbol``.

It carries Eurex keys (``EUX:DE000F338MM9``) and Euronext ones
(``XNT:DBRU:ABI-C-202612-60``) as well as OCC symbols, so the old name
contradicted its own contents. The database column was renamed in migration
0002; this is the wire format catching up, and it is a frontend contract too,
which is what these assertions are guarding.
"""

from app.routers import chain as chain_router
from app.schemas.chain import ContractQuote, ContractSeriesResponse, SessionStrikePoint


def test_every_contract_shaped_response_names_the_key_symbol():
    for model in (ContractQuote, ContractSeriesResponse, SessionStrikePoint):
        assert "symbol" in model.model_fields, model.__name__
        assert "occ_symbol" not in model.model_fields, model.__name__


def test_the_contract_series_route_takes_a_symbol():
    paths = {route.path for route in chain_router.router.routes}
    assert "/api/chains/contracts/{symbol}" in paths
    assert "/api/chains/contracts/{occ_symbol}" not in paths
