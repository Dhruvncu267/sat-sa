// SAT-SA Firebase web configuration.
// This is NOT a secret -- Firebase's own security model relies on
// Authentication + Firestore security rules, not on hiding this object.
// (The real secret is server/serviceAccountKey.json, which never leaves
// the backend and is never sent to a browser.)
export const firebaseConfig = {
  apiKey: "AIzaSyDtPYFNN7uoMz37My9ij9_TMu5LZkNuI6Q",
  authDomain: "sat-sa.firebaseapp.com",
  projectId: "sat-sa",
  storageBucket: "sat-sa.firebasestorage.app",
  messagingSenderId: "1084324711110",
  appId: "1:1084324711110:web:e8a98d6675147408a1a5c7",
};
