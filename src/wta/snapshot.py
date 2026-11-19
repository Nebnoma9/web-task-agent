"""Compact accessibility snapshot.

We walk the live DOM in the page, compute an ARIA-ish role and accessible name for every
visible element that matters (interactive controls, headings, landmarks, alerts, short text),
and tag each one with ``data-wta-ref="eN"``. The policy refers to elements by these refs, which
makes actions unambiguous and auditable; the executor resolves a ref with a CSS attribute
selector. Refs are re-assigned on every observation, so a stale ref fails loudly instead of
clicking the wrong thing.

The snapshot rendered for the model is a few hundred tokens for a typical page, versus tens of
thousands for raw HTML.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

REF_ATTR = "data-wta-ref"

# Runs inside the page. Kept dependency-free and deterministic (document order).
_SNAPSHOT_JS = r"""
({ maxNodes, maxText, refAttr }) => {
  document.querySelectorAll('[' + refAttr + ']').forEach(el => el.removeAttribute(refAttr));

  const clean = (s) => (s || '').replace(/\s+/g, ' ').trim();
  const clip = (s) => (s.length > maxText ? s.slice(0, maxText - 1) + '…' : s);

  const visible = (el) => {
    if (el.closest('[hidden], [aria-hidden="true"]')) return false;
    const st = getComputedStyle(el);
    if (st.display === 'none' || st.visibility === 'hidden' || st.opacity === '0') return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };

  const implicitRole = (el) => {
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (tag === 'a' && el.hasAttribute('href')) return 'link';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'input') {
      if (['button', 'submit', 'reset', 'image'].includes(type)) return 'button';
      if (type === 'checkbox') return 'checkbox';
      if (type === 'radio') return 'radio';
      if (type === 'search') return 'searchbox';
      if (type === 'number') return 'spinbutton';
      if (type === 'hidden') return null;
      return 'textbox';
    }
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'img' && el.getAttribute('alt')) return 'img';
    if (tag === 'table') return 'table';
    if (tag === 'nav') return 'navigation';
    if (tag === 'form' && (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby'))) return 'form';
    return null;
  };

  const labelText = (el) => {
    const lb = el.getAttribute('aria-labelledby');
    if (lb) {
      const t = lb.split(/\s+/).map(id => document.getElementById(id)).filter(Boolean)
        .map(n => n.textContent).join(' ');
      if (clean(t)) return clean(t);
    }
    const al = el.getAttribute('aria-label');
    if (al && clean(al)) return clean(al);
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      if (l && clean(l.textContent)) return clean(l.textContent);
    }
    const wrap = el.closest('label');
    if (wrap && clean(wrap.textContent)) return clean(wrap.textContent);
    return '';
  };

  const accName = (el, role) => {
    const l = labelText(el);
    if (l) return l;
    if (['textbox', 'searchbox', 'spinbutton', 'combobox'].includes(role)) {
      return clean(el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name'));
    }
    if (role === 'img') return clean(el.getAttribute('alt'));
    if (el.tagName === 'INPUT') return clean(el.value || el.getAttribute('title'));
    return clean(el.innerText || el.textContent || el.getAttribute('title'));
  };

  const INTERACTIVE = new Set(['link', 'button', 'textbox', 'searchbox', 'spinbutton', 'combobox',
    'checkbox', 'radio', 'switch', 'tab', 'menuitem', 'option']);
  const STRUCTURAL = new Set(['heading', 'img', 'alert', 'status', 'navigation', 'form', 'table',
    'search', 'toolbar', 'dialog']);
  const LANDMARKS = new Set(['navigation', 'form', 'table', 'search', 'toolbar', 'dialog']);
  const INLINE_TAGS = new Set(['STRONG', 'EM', 'B', 'I', 'CODE', 'SMALL', 'BR', 'MARK', 'ABBR', 'SPAN']);
  const TEXT_TAGS = new Set(['P', 'LI', 'TD', 'TH', 'SPAN', 'DIV', 'STRONG', 'EM', 'LEGEND', 'DT', 'DD']);

  const nodes = [];
  let truncated = false;
  let n = 0;
  const emit = (el, node) => {
    if (nodes.length >= maxNodes) { truncated = true; return; }
    n += 1;
    node.ref = 'e' + n;
    el.setAttribute(refAttr, node.ref);
    nodes.push(node);
  };

  const ownText = (el) => clean(Array.from(el.childNodes)
    .filter(c => c.nodeType === Node.TEXT_NODE).map(c => c.textContent).join(' '));

  const walk = (el, insideNamed) => {
    if (!(el instanceof Element)) return;
    const tag = el.tagName;
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'SVG', 'HEAD'].includes(tag)) return;
    if (!visible(el) && tag !== 'BODY' && tag !== 'OPTION') return;

    const role = (el.getAttribute('role') || implicitRole(el) || '').toLowerCase() || null;
    let named = insideNamed;
    if (role && (INTERACTIVE.has(role) || STRUCTURAL.has(role))) {
      const landmark = LANDMARKS.has(role);
      const node = { role, name: clip(landmark ? labelText(el) : accName(el, role)) };
      if (role === 'heading') node.level = parseInt(tag.slice(1), 10) || undefined;
      if (['textbox', 'searchbox', 'spinbutton'].includes(role)) {
        node.value = el.type === 'password' ? (el.value ? '••••' : '') : clip(el.value || '');
        if (el.type === 'password') node.secret = true;
        const ac = el.getAttribute('autocomplete') || '';
        if (ac.startsWith('cc-')) node.payment = true;
      }
      if (role === 'combobox' && tag === 'SELECT') {
        node.value = clean(el.options[el.selectedIndex] ? el.options[el.selectedIndex].text : '');
        node.options = Array.from(el.options).map(o => clip(clean(o.text))).slice(0, 25);
      }
      if (role === 'checkbox' || role === 'radio') node.checked = !!el.checked;
      if (el.disabled || el.getAttribute('aria-disabled') === 'true') node.disabled = true;
      if (el.getAttribute('aria-invalid') === 'true') node.invalid = true;
      const emptyLive = (role === 'alert' || role === 'status') && !node.name;
      if (!emptyLive) emit(el, node);  // empty live regions are noise until they say something
      if (INTERACTIVE.has(role)) return;  // the accessible name already summarises the children
      if (!landmark && !emptyLive) named = true;  // suppress duplicate text nodes below
    } else if (!insideNamed && TEXT_TAGS.has(tag)) {
      // A paragraph made only of text + inline formatting reads as one line, e.g.
      // "Use test card <strong>4242…</strong>" -> one text node, not two fragments.
      const inlineOnly = Array.from(el.children).every(c => INLINE_TAGS.has(c.tagName));
      const t = inlineOnly ? clean(el.innerText) : ownText(el);
      if (t && t.length > 1) emit(el, { role: 'text', name: clip(t) });
      if (inlineOnly) return;
    }
    for (const child of el.children) walk(child, named);
  };

  walk(document.body, false);
  return { url: location.href, title: document.title, nodes, truncated };
}
"""


@dataclass(slots=True)
class SnapshotNode:
    ref: str
    role: str
    name: str
    value: str | None = None
    level: int | None = None
    options: list[str] | None = None
    checked: bool | None = None
    disabled: bool = False
    invalid: bool = False
    secret: bool = False
    payment: bool = False

    def render(self) -> str:
        parts = [f"[{self.ref}] {self.role}"]
        if self.name:
            parts.append(f'"{self.name}"')
        if self.level:
            parts.append(f"level={self.level}")
        if self.value is not None:
            parts.append(f'value="{self.value}"')
        if self.options:
            parts.append("options=" + "|".join(self.options))
        if self.checked is not None:
            parts.append("checked" if self.checked else "unchecked")
        if self.disabled:
            parts.append("disabled")
        if self.invalid:
            parts.append("invalid")
        if self.payment:
            parts.append("payment-field")
        return " ".join(parts)


@dataclass(slots=True)
class Snapshot:
    url: str
    title: str
    nodes: list[SnapshotNode] = field(default_factory=list)
    truncated: bool = False

    def render(self) -> str:
        lines = [f"URL: {self.url}", f"Title: {self.title}"]
        lines.extend(n.render() for n in self.nodes)
        if self.truncated:
            lines.append("… (snapshot truncated; scroll or narrow the page to see more)")
        return "\n".join(lines)

    def find(
        self, role: str, name: str | None = None, name_contains: str | None = None
    ) -> list[SnapshotNode]:
        """Find nodes by role (``textbox`` also matches ``searchbox``/``spinbutton``)."""
        roles = {role}
        if role == "textbox":
            roles |= {"searchbox", "spinbutton"}
        out = []
        for n in self.nodes:
            if n.role not in roles:
                continue
            if name is not None and n.name.lower() != name.lower():
                continue
            if name_contains is not None and name_contains.lower() not in n.name.lower():
                continue
            out.append(n)
        return out

    def by_ref(self, ref: str) -> SnapshotNode | None:
        return next((n for n in self.nodes if n.ref == ref), None)

    @property
    def fingerprint(self) -> str:
        """Stable hash of what the agent can perceive; used to detect 'no visible effect'."""
        h = hashlib.sha256(self.url.encode())
        for n in self.nodes:
            h.update(f"{n.role}|{n.name}|{n.value}|{n.checked}".encode())
        return h.hexdigest()[:16]

    def summary(self) -> str:
        roles: dict[str, int] = {}
        for n in self.nodes:
            roles[n.role] = roles.get(n.role, 0) + 1
        top = ", ".join(f"{k}={v}" for k, v in sorted(roles.items(), key=lambda kv: -kv[1])[:5])
        return f"{self.title!r} {self.url} | {len(self.nodes)} nodes ({top})"


def _node_from_dict(d: dict[str, Any]) -> SnapshotNode:
    return SnapshotNode(
        ref=str(d["ref"]),
        role=str(d["role"]),
        name=str(d.get("name") or ""),
        value=d.get("value"),
        level=d.get("level"),
        options=d.get("options"),
        checked=d.get("checked"),
        disabled=bool(d.get("disabled", False)),
        invalid=bool(d.get("invalid", False)),
        secret=bool(d.get("secret", False)),
        payment=bool(d.get("payment", False)),
    )


async def build_snapshot(page: Page, *, max_nodes: int = 250, max_text: int = 120) -> Snapshot:
    """Build a compact snapshot of ``page`` and tag elements with ``data-wta-ref``."""
    raw: dict[str, Any] = await page.evaluate(
        _SNAPSHOT_JS, {"maxNodes": max_nodes, "maxText": max_text, "refAttr": REF_ATTR}
    )
    return Snapshot(
        url=str(raw["url"]),
        title=str(raw["title"]),
        nodes=[_node_from_dict(d) for d in raw["nodes"]],
        truncated=bool(raw["truncated"]),
    )
