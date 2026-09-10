// SAT-SA supervisor login. Signs in directly against Firebase Auth --
// this backend never sees the password. On success, redirects to the
// dashboard, which will pick up the signed-in user itself.
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-app.js";
import {
  getAuth, signInWithEmailAndPassword, setPersistence, browserSessionPersistence,
} from "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js";
import { firebaseConfig } from "./firebase-config.js";

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
// Session-only persistence: a login is only remembered for as long as this
// browser tab/window stays open. Closing the browser (or the tab) and
// reopening the site always requires logging in again -- this is
// intentional, requested for security, instead of Firebase's default of
// staying logged in indefinitely across browser restarts.
await setPersistence(auth, browserSessionPersistence);

const form = document.getElementById('loginForm');
const msg = document.getElementById('loginMsg');
const submitBtn = document.getElementById('loginSubmit');
const pwInput = document.getElementById('loginPassword');
const pwToggle = document.getElementById('pwToggle');

pwToggle.addEventListener('click', () => {
  const show = pwInput.type === 'password';
  pwInput.type = show ? 'text' : 'password';
  pwToggle.textContent = show ? 'Hide' : 'Show';
});

// Firebase error codes are technical (auth/wrong-password, auth/user-not-found,
// auth/invalid-credential, auth/too-many-requests, ...) -- the supervisor
// should only ever see one consistent message for a bad login, exactly as
// specified, and a distinct one only for the rare "too many attempts" case.
function friendlyError(err) {
  if (err && err.code === 'auth/too-many-requests') {
    return 'Too many failed attempts. Please wait a few minutes and try again.';
  }
  if (err && err.code === 'auth/network-request-failed') {
    return 'Could not reach the login server. Check your internet connection.';
  }
  return 'Invalid credentials. Authorized supervisor access only.';
}

form.addEventListener('submit', async (ev) => {
  ev.preventDefault();
  const email = document.getElementById('loginEmail').value.trim();
  const password = pwInput.value;
  msg.textContent = '';
  msg.className = 'upload-msg';
  submitBtn.disabled = true;
  submitBtn.textContent = 'Checking…';
  try {
    await signInWithEmailAndPassword(auth, email, password);
    // Also confirm the backend recognizes this account as an active
    // supervisor before leaving the login page (a Firebase account that
    // exists but was never provisioned via create_supervisor.py, or was
    // deactivated, should not reach the dashboard).
    const token = await auth.currentUser.getIdToken();
    const res = await fetch('/api/session', { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) {
      await auth.signOut();
      throw { code: 'not-authorized' };
    }
    window.location.href = '/';
  } catch (err) {
    msg.textContent = err && err.code === 'not-authorized'
      ? 'This account is not an authorized supervisor.'
      : friendlyError(err);
    msg.className = 'upload-msg err';
    submitBtn.disabled = false;
    submitBtn.textContent = 'Login';
  }
});
