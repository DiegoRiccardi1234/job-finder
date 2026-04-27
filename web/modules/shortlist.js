// Role shortlist API wrapper. Persists the list of target roles the user
// accumulates by clicking chat pills; the list is auto-filled in Step 2.

import { api } from "./helpers.js";

export async function loadShortlist() {
  try {
    const payload = await api("/api/roles/shortlist");
    return Array.isArray(payload.roles) ? payload.roles : [];
  } catch (error) {
    console.warn("role shortlist load failed", error);
    return [];
  }
}

export async function addToShortlist(roles) {
  const clean = (roles || []).filter(Boolean).map(String);
  if (!clean.length) return [];
  try {
    const payload = await api("/api/roles/shortlist", {
      method: "POST",
      body: JSON.stringify({ roles: clean }),
    });
    return Array.isArray(payload.roles) ? payload.roles : clean;
  } catch (error) {
    console.warn("role shortlist persist failed", error);
    return clean;
  }
}
