// Shared mutable app state, so feature modules can read/write the same values
// app.js does (ES modules don't share lexical scope). Keep this tiny: only
// genuinely cross-module state belongs here.
export const appState = {
  // id of the job currently shown in the detail panel (null when closed)
  selectedJobId: null,
  // optional-feature on/off flags, populated from /api/health preferences
  featureFlags: {},
};
