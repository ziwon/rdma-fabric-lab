// The site route of the page that shows a scenario's checks.
const TRACK_DIRS: Record<string, string> = {
  'a-soft-roce': 'track-a',
  'b-ibsim': 'track-b',
  'c-ns3': 'track-c',
  cel1: 'cel1',
};

// make collect has no lab of its own. The version lock page explains it.
const ELSEWHERE: Record<string, string> = {
  'a-soft-roce/vm-environment': '/repository/version-lock/',
};

export function labRoute(track: string, scenario: string): string {
  const elsewhere = ELSEWHERE[`${track}/${scenario}`];
  if (elsewhere) return elsewhere;
  const dir = TRACK_DIRS[track];
  if (!dir) throw new Error(`labRoute: no lab directory for track ${track}`);
  return `/labs/${dir}/${scenario}/`;
}
