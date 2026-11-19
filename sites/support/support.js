// Helpdesk: a fictional support portal. Session and tickets live in localStorage.
const SESSION_KEY = "hd_session";
const TICKETS_KEY = "hd_tickets";

function getSession() {
  try {
    return JSON.parse(localStorage.getItem(SESSION_KEY) || "null");
  } catch (e) {
    return null;
  }
}

function requireSession() {
  const s = getSession();
  if (!s) {
    location.href = "login.html?next=" + encodeURIComponent(location.pathname.split("/").pop());
    throw new Error("redirecting to login");
  }
  return s;
}

function getTickets() {
  try {
    return JSON.parse(localStorage.getItem(TICKETS_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function saveTickets(t) {
  localStorage.setItem(TICKETS_KEY, JSON.stringify(t));
}

function mountHeader(session) {
  const who = session
    ? `<span class="muted" id="whoami">Signed in as ${session.username}</span>
       <a href="tickets.html">My tickets</a><a href="new-ticket.html">New ticket</a>
       <a href="#" id="logout">Sign out</a>`
    : "";
  document.body.insertAdjacentHTML(
    "afterbegin",
    `<header class="site"><a href="tickets.html">Helpdesk</a><nav aria-label="Account">${who}</nav></header>`
  );
  const lo = document.getElementById("logout");
  if (lo)
    lo.addEventListener("click", (e) => {
      e.preventDefault();
      localStorage.removeItem(SESSION_KEY);
      location.href = "login.html";
    });
}
