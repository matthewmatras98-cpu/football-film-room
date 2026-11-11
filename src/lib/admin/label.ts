/** "AWY @ HOM, Sun 11/22 11:00 AM MST" */
export function gameMenuLabel(game: { away: string; home: string; kickoff_utc: string }): string {
  return `${game.away} @ ${game.home}, ${kickoffMenu(game.kickoff_utc)}`;
}

export function kickoffMenu(iso: string): string {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'America/Phoenix',
    weekday: 'short',
    month: 'numeric',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  }).formatToParts(new Date(iso));
  const part = (type: Intl.DateTimeFormatPartTypes) => parts.find((item) => item.type === type)?.value ?? '';
  return `${part('weekday')} ${part('month')}/${part('day')} ${part('hour')}:${part('minute')} ${part('dayPeriod')} MST`;
}
