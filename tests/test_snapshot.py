from __future__ import annotations

import pytest
from playwright.async_api import Browser

from wta.actions import Click, Select
from wta.snapshot import REF_ATTR, build_snapshot

pytestmark = pytest.mark.browser

FIXTURE = """
<html><head><title>Fixture</title></head><body>
  <nav aria-label="Main"><a href="/a">Home</a> <a href="/b">Cart (0)</a></nav>
  <h1>Checkout</h1>
  <p>Use test card <strong>4242 4242 4242 4242</strong>.</p>
  <form aria-label="Login">
    <label for="u">Username</label><input id="u" value="alice">
    <label for="p">Password</label><input id="p" type="password" value="s3cret!">
    <input aria-label="Search" type="search" placeholder="Find…">
    <input id="cc" autocomplete="cc-number" aria-label="Card number">
    <select aria-label="Country"><option>France</option><option selected>Spain</option></select>
    <label><input type="checkbox" checked> Remember me</label>
    <button type="submit" disabled>Sign in</button>
  </form>
  <div hidden><button>Hidden button</button></div>
  <div style="display:none"><a href="/x">Invisible link</a></div>
  <p role="alert"></p>
  <p role="status">Saved!</p>
  <img src="data:," alt="Logo" width="10" height="10">
</body></html>
"""


async def _snap(browser: Browser, html: str = FIXTURE):
    page = await browser.new_page()
    await page.set_content(html)
    return page, await build_snapshot(page)


async def test_roles_names_and_values(browser: Browser) -> None:
    page, snap = await _snap(browser)
    try:
        rendered = snap.render()
        assert snap.title == "Fixture"
        assert snap.find("link", name="Cart (0)")
        assert snap.find("heading", name="Checkout")[0].level == 1
        assert snap.find("textbox", name="Username")[0].value == "alice"
        assert snap.find("searchbox", name="Search")
        combo = snap.find("combobox", name="Country")[0]
        assert combo.value == "Spain"
        assert combo.options == ["France", "Spain"]
        assert snap.find("checkbox", name="Remember me")[0].checked is True
        assert snap.find("button", name="Sign in")[0].disabled is True
        assert snap.find("status", name="Saved!")
        assert snap.find("img", name="Logo")
        assert snap.find("textbox", name="Card number")[0].payment is True
        assert 'text "Use test card 4242 4242 4242 4242."' in rendered
    finally:
        await page.close()


async def test_password_values_are_never_exposed(browser: Browser) -> None:
    page, snap = await _snap(browser)
    try:
        pw = snap.find("textbox", name="Password")[0]
        assert pw.secret is True
        assert pw.value == "••••"
        assert "s3cret!" not in snap.render()
    finally:
        await page.close()


async def test_hidden_and_empty_elements_are_skipped(browser: Browser) -> None:
    page, snap = await _snap(browser)
    try:
        rendered = snap.render()
        assert "Hidden button" not in rendered
        assert "Invisible link" not in rendered
        assert not snap.find("alert")  # empty live region is noise
    finally:
        await page.close()


async def test_refs_are_unique_sequential_and_tagged(browser: Browser) -> None:
    page, snap = await _snap(browser)
    try:
        refs = [n.ref for n in snap.nodes]
        assert refs == [f"e{i}" for i in range(1, len(refs) + 1)]
        for n in snap.nodes:
            assert await page.locator(f'[{REF_ATTR}="{n.ref}"]').count() == 1
        # Rebuilding re-assigns refs and removes stale tags.
        snap2 = await build_snapshot(page)
        assert [n.ref for n in snap2.nodes] == refs
        assert await page.locator(f"[{REF_ATTR}]").count() == len(refs)
    finally:
        await page.close()


async def test_truncation(browser: Browser) -> None:
    html = "<body>" + "".join(f"<button>b{i}</button>" for i in range(50)) + "</body>"
    page = await browser.new_page()
    try:
        await page.set_content(html)
        snap = await build_snapshot(page, max_nodes=10)
        assert len(snap.nodes) == 10
        assert snap.truncated
        assert "truncated" in snap.render()
    finally:
        await page.close()


async def test_fingerprint_changes_with_state(browser: Browser) -> None:
    page, snap = await _snap(browser)
    try:
        await page.fill("#u", "bob")
        snap2 = await build_snapshot(page)
        assert snap.fingerprint != snap2.fingerprint
    finally:
        await page.close()


async def test_executor_verifies_effects_on_demo_site(make_session) -> None:
    from wta.actions import Goto

    session = await make_session()
    await session.execute(Goto(url="/dashboard/index.html"))
    snap = await session.snapshot()

    export = snap.find("button", name="Export CSV")[0]
    res = await session.execute(Click(ref=export.ref))
    assert res.ok and res.effect == "no_visible_change"  # the intentional regression
    obs = await session.observe()
    assert any("buildCsv" in e for e in obs.console_errors)

    refresh = obs.snapshot.find("button", name="Refresh data")[0]
    res = await session.execute(Click(ref=refresh.ref))
    assert res.effect == "changed"

    snap = await session.snapshot()
    rng = snap.find("combobox", name="Range")[0]
    assert (await session.execute(Select(ref=rng.ref, option="Last 30 days"))).ok
    assert await session.text_visible("4,971")

    stale = await session.execute(Click(ref="e999"))
    assert not stale.ok and "not found" in (stale.error or "")
