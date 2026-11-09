/**
 * Single source for the spec §7 credit lines and the §11 disclaimer.
 * The footer and /methodology/ both render from these constants.
 */

export const dataCredit =
  'Data: nflverse (nflfastR / nflreadr), licensed CC-BY 4.0. NFL data belongs to its respective owners.';

export const chartingCredit =
  'Charting data: FTN Data via nflverse, licensed CC-BY-SA 4.0. Any published table derived from FTN data is also CC-BY-SA. Label those tables so readers can see it.';

export const ngsCredit =
  'Next Gen Stats data via nflverse; NGS is a product of the NFL.';

export const oddsCredit =
  'Odds data: The Odds API. Lines shown are medians of N US books.';

export const fullCredits = [dataCredit, chartingCredit, ngsCredit, oddsCredit] as const;

/** Compact footer line. Same sources as fullCredits, without the longer clauses. */
export const footerCredit =
  'Data: nflverse (CC-BY 4.0). Charting: FTN Data via nflverse (CC-BY-SA 4.0). Next Gen Stats via nflverse. Odds: The Odds API.';

export const disclaimer =
  'For entertainment and analysis only. Not betting advice. No sportsbook affiliation. 21+. If you or someone you know has a gambling problem, call 1-800-GAMBLER.';
