"""All visual directions share one sitemap and meet a professional craft bar."""
from __future__ import annotations

import random

from agents.picture import themes


def _plan():
    return {
        "screens": [
            {"name": "Storefront Home", "route": "/", "purpose": "Discover products"},
            {"name": "Shop", "route": "/shop", "purpose": "Search, filter and add products"},
            {"name": "Product Details", "route": "/products/[id]",
             "purpose": "Inspect and purchase a real product"},
            {"name": "Admin Dashboard", "route": "/admin", "who": ["admin"],
             "purpose": "Manage products, orders and customers"},
        ],
    }


def test_controller_selects_one_canonical_sitemap_for_every_direction():
    site_map = themes.site_map_from_plan(_plan(), {})
    preview = themes.representative_pages(site_map)
    directions = themes.random_directions(5, random.Random(7))
    for direction in directions:
        direction["site_map"] = site_map
        direction["preview_pages"] = preview

    assert [page["name"] for page in site_map] == [
        "Storefront Home", "Shop", "Product Details", "Admin Dashboard"]
    assert preview[0]["name"] == "Storefront Home"
    assert preview[-1]["name"] == "Admin Dashboard"
    contracts = [themes.sitemap_prompt(d["site_map"], d["preview_pages"])
                 for d in directions]
    assert len(set(contracts)) == 1
    assert 'data-page="storefront-home"' in contracts[0]
    assert "add no fourth page" in contracts[0]


def test_model_page_names_are_aligned_to_the_canonical_contract():
    preview = themes.representative_pages(themes.site_map_from_plan(_plan(), {}))
    raw = """<!doctype html><html><body><main>
      <nav><button data-goto="landing">Home</button><button data-goto="catalog">Shop</button><button data-goto="backoffice">Admin</button></nav>
      <section data-page="landing"><h1>Home</h1></section>
      <section data-page="catalog"><h1>Shop</h1></section>
      <section data-page="backoffice"><h1>Admin</h1></section>
    </main></body></html>"""

    aligned = themes.align_preview_sitemap(raw, preview)

    assert themes.sitemap_issue(aligned, preview) == ""
    assert themes.pages_of(aligned) == [page["name"] for page in preview]
    for page in preview:
        assert f'data-goto="{page["slug"]}"' in aligned


def test_wrong_screen_count_is_rejected_instead_of_showing_a_broken_sitemap():
    preview = themes.representative_pages(themes.site_map_from_plan(_plan(), {}))
    html = '<section data-page="only-one"></section>'

    issue = themes.sitemap_issue(html, preview)

    assert "expected 3 canonical screens" in issue


def test_five_directions_are_coherent_curated_systems_not_random_style_parts():
    directions = themes.random_directions(5, random.Random(11))

    assert len(directions) == 5
    assert len({row["palette"] for row in directions}) == 5
    assert all(row.get("quality") for row in directions)
    assert "ULTRA-PROFESSIONAL FINISH" in themes.SYSTEM
    assert "generated-template tells" in themes.SYSTEM
    assert "12-column grid" in themes.SYSTEM
