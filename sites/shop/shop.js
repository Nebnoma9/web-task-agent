// Northwind Gear: a fictional shop used as an agent test bed. All data is made up.
const CATALOG = [
  { id: "p-101", name: "Aurora Wireless Headphones", category: "headphones", price: 89.0, desc: "Over-ear, 30 h battery, active noise cancelling." },
  { id: "p-102", name: "Pulse Sport Earbuds", category: "headphones", price: 39.9, desc: "Sweat-resistant earbuds with a secure fit." },
  { id: "p-103", name: "Studio Monitor Headphones", category: "headphones", price: 129.0, desc: "Closed-back, flat response for mixing." },
  { id: "p-104", name: "Basic Wired Headphones", category: "headphones", price: 19.5, desc: "Lightweight wired headphones with inline mic." },
  { id: "p-201", name: "Copper Gooseneck Kettle", category: "kettle", price: 64.0, desc: "Precision pour spout, 1.0 L." },
  { id: "p-202", name: "Compact Travel Kettle", category: "kettle", price: 34.9, desc: "Folding silicone body, dual voltage, 0.6 L." },
  { id: "p-203", name: "Glass Electric Kettle", category: "kettle", price: 49.0, desc: "Borosilicate glass, 1.7 L, auto shut-off." },
  { id: "p-301", name: "Trail Water Bottle", category: "outdoor", price: 14.0, desc: "750 ml, BPA-free, leak-proof cap." },
  { id: "p-302", name: "Insulated Camping Mug", category: "outdoor", price: 18.5, desc: "Keeps drinks hot for 6 hours." },
];

const CART_KEY = "nw_cart";
const ORDER_KEY = "nw_last_order";
const ORDER_SEQ_KEY = "nw_order_seq";

function money(v) {
  return "€" + v.toFixed(2);
}

function getCart() {
  try {
    return JSON.parse(localStorage.getItem(CART_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function saveCart(cart) {
  localStorage.setItem(CART_KEY, JSON.stringify(cart));
  renderCartCount();
}

function addToCart(id, qty) {
  const cart = getCart();
  const line = cart.find((l) => l.id === id);
  if (line) line.qty += qty;
  else cart.push({ id, qty });
  saveCart(cart);
}

function cartCount() {
  return getCart().reduce((n, l) => n + l.qty, 0);
}

function cartTotal() {
  return getCart().reduce((sum, l) => {
    const p = CATALOG.find((x) => x.id === l.id);
    return sum + (p ? p.price * l.qty : 0);
  }, 0);
}

function renderCartCount() {
  const el = document.getElementById("cart-link");
  if (el) el.textContent = "Cart (" + cartCount() + ")";
}

function header() {
  return `
  <header class="site">
    <a href="index.html">Northwind Gear</a>
    <nav aria-label="Main">
      <a href="index.html">Shop</a>
      <a id="cart-link" href="cart.html">Cart (0)</a>
    </nav>
  </header>`;
}

function mountHeader() {
  document.body.insertAdjacentHTML("afterbegin", header());
  renderCartCount();
}

function luhnValid(num) {
  const digits = num.replace(/[\s-]/g, "");
  if (!/^\d{13,19}$/.test(digits)) return false;
  let sum = 0;
  let dbl = false;
  for (let i = digits.length - 1; i >= 0; i--) {
    let d = parseInt(digits[i], 10);
    if (dbl) {
      d *= 2;
      if (d > 9) d -= 9;
    }
    sum += d;
    dbl = !dbl;
  }
  return sum % 10 === 0;
}
