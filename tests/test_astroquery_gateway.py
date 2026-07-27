"""Regression tests for IRSA query integration."""

from __future__ import annotations  # noqa: I001

from unittest.mock import Mock

import astropy.units as u  # type: ignore[import-not-found]
import pytest  # type: ignore[import-not-found]
from astroquery.ipac.irsa import Irsa  # type: ignore[import-not-found]
from astropy.table import Table  # type: ignore[import-not-found]

import science_assistant.services.astroquery_gateway as gateway  # type: ignore


def test_irsa_query_uses_explicit_radius_columns_and_row_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify explicit IRSA cone parameters and TAP row limit.

    Args:
        monkeypatch: Pytest patch helper.

    Returns:
        None.
    """
    expected = Table({"ra": [10.0], "dec": [41.0]})
    query_region = Mock(return_value="SELECT ra,dec FROM fp_psc")
    tap_result = Mock()
    tap_result.to_table.return_value = expected
    query_tap = Mock(return_value=tap_result)
    monkeypatch.setattr(Irsa, "query_region", query_region)
    monkeypatch.setattr(Irsa, "query_tap", query_tap)

    result = gateway.query_object(
        service="irsa",
        target=None,
        ra_deg=10.6847083,
        dec_deg=41.26875,
        radius_arcmin=1.5,
        catalog="fp_psc",
        columns=["ra", "dec"],
        row_limit=5,
    )

    region_kwargs = query_region.call_args.kwargs
    assert result is expected
    assert region_kwargs["catalog"] == "fp_psc"
    assert region_kwargs["spatial"] == "Cone"
    assert region_kwargs["columns"] == "ra,dec"
    assert region_kwargs["get_query_payload"] is True
    assert region_kwargs["radius"].to_value(u.arcmin) == pytest.approx(1.5)

    query_tap.assert_called_once_with(query="SELECT ra,dec FROM fp_psc", maxrec=5)


def test_irsa_query_uses_default_radius_columns_and_unlimited_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify default IRSA radius, columns, and unlimited TAP query.

    Args:
        monkeypatch: Pytest patch helper.

    Returns:
        None.
    """
    expected = Table({"ra": [10.0]})
    query_region = Mock(return_value="SELECT * FROM fp_psc")
    tap_result = Mock()
    tap_result.to_table.return_value = expected
    query_tap = Mock(return_value=tap_result)
    monkeypatch.setattr(Irsa, "query_region", query_region)
    monkeypatch.setattr(Irsa, "query_tap", query_tap)

    result = gateway.query_object(
        service="irsa",
        target=None,
        ra_deg=10.6847083,
        dec_deg=41.26875,
        radius_arcmin=None,
        catalog="fp_psc",
        columns=None,
        row_limit=-1,
    )

    region_kwargs = query_region.call_args.kwargs
    assert result is expected

    assert region_kwargs["columns"] == "*"
    assert region_kwargs["radius"].to_value(u.arcsec) == pytest.approx(10.0)
    query_tap.assert_called_once_with(query="SELECT * FROM fp_psc", maxrec=None)
