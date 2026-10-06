//! The tenant time zones a schedule can be read in
//! (`backend/api/v1/timezone.py::SUPPORTED`), with the rules Python's
//! `zoneinfo` applies to them from 2023 on (tzdata 2026.2; every offset
//! change zoneinfo reports for 2025-2075 is checked by `zones_match_zoneinfo`).
//!
//! Ten of the thirteen zones have had a fixed offset since 2022 or earlier.
//! Three observe daylight saving time:
//! * Europe/Madrid    CET/CEST, last Sunday of March / October at 01:00 UTC
//! * America/New_York EST/EDT, 2nd Sunday of March 07:00 UTC / 1st Sunday of
//!                    November 06:00 UTC
//! * America/Santiago -04/-03, first Sunday on or after April 2 at 03:00 UTC
//!                    (back to -04) / September 2 at 04:00 UTC (to -03)
//!
//! A tzdata release that changes one of these rules has to be mirrored here;
//! the differential test is how that would be noticed.

use chrono::{Datelike, Duration, NaiveDate, NaiveDateTime, Weekday};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Zone {
    Fixed(i32),
    Madrid,
    NewYork,
    Santiago,
}

/// `timezone.py::DEFAULT_TZ`.
pub const DEFAULT_TZ: &str = "America/Costa_Rica";

impl Zone {
    /// The zone for a SUPPORTED name; `None` for anything else.
    pub fn from_name(name: &str) -> Option<Zone> {
        const H: i32 = 3600;
        Some(match name {
            "America/Costa_Rica" | "America/Mexico_City" | "America/Guatemala" => Zone::Fixed(-6 * H),
            "America/Bogota" | "America/Lima" | "America/Guayaquil" | "America/Panama" => Zone::Fixed(-5 * H),
            "America/Santo_Domingo" => Zone::Fixed(-4 * H),
            "America/Argentina/Buenos_Aires" => Zone::Fixed(-3 * H),
            "UTC" => Zone::Fixed(0),
            "Europe/Madrid" => Zone::Madrid,
            "America/New_York" => Zone::NewYork,
            "America/Santiago" => Zone::Santiago,
            _ => return None,
        })
    }

    /// `utcoffset()` of the instant `utc`, in seconds.
    pub fn offset_at_utc(&self, utc: NaiveDateTime) -> i32 {
        const H: i32 = 3600;
        let y = utc.year();
        match self {
            Zone::Fixed(o) => *o,
            Zone::Madrid => {
                let start = last_sunday(y, 3).and_hms_opt(1, 0, 0).expect("valid");
                let end = last_sunday(y, 10).and_hms_opt(1, 0, 0).expect("valid");
                if utc >= start && utc < end { 2 * H } else { H }
            }
            Zone::NewYork => {
                let start = nth_sunday_on_or_after(y, 3, 8).and_hms_opt(7, 0, 0).expect("valid");
                let end = nth_sunday_on_or_after(y, 11, 1).and_hms_opt(6, 0, 0).expect("valid");
                if utc >= start && utc < end { -4 * H } else { -5 * H }
            }
            Zone::Santiago => {
                let dst_end = nth_sunday_on_or_after(y, 4, 2).and_hms_opt(3, 0, 0).expect("valid");
                let dst_start = nth_sunday_on_or_after(y, 9, 2).and_hms_opt(4, 0, 0).expect("valid");
                if utc < dst_end || utc >= dst_start { -3 * H } else { -4 * H }
            }
        }
    }

    /// `local.replace(tzinfo=zone, fold=fold).utcoffset()`: in a repeated
    /// hour fold 0 is the first pass (the offset before the change); in a
    /// skipped hour fold 0 also takes the offset before the change, which
    /// is what makes `datetime_exists` false there.
    pub fn local_offset(&self, local: NaiveDateTime, fold: u8) -> i32 {
        let before = self.offset_at_utc(local - Duration::days(1));
        let after = self.offset_at_utc(local + Duration::days(1));
        if before == after {
            return before;
        }
        let valid = |o: i32| self.offset_at_utc(local - Duration::seconds(i64::from(o))) == o;
        match (valid(before), valid(after)) {
            (true, true) | (false, false) => if fold == 0 { before } else { after },
            (true, false) => before,
            (false, true) => after,
        }
    }

    /// `dateutil.tz.datetime_exists` for a fold-0 wall-clock time.
    pub fn exists(&self, local: NaiveDateTime) -> bool {
        let utc = local - Duration::seconds(i64::from(self.local_offset(local, 0)));
        utc + Duration::seconds(i64::from(self.offset_at_utc(utc))) == local
    }
}

fn last_sunday(year: i32, month: u32) -> NaiveDate {
    let next = if month == 12 {
        NaiveDate::from_ymd_opt(year + 1, 1, 1)
    } else {
        NaiveDate::from_ymd_opt(year, month + 1, 1)
    }
    .expect("valid");
    let mut d = next - Duration::days(1);
    while d.weekday() != Weekday::Sun {
        d -= Duration::days(1);
    }
    d
}

/// The first Sunday on or after `year-month-day` (tzdata's `Sun>=N`).
fn nth_sunday_on_or_after(year: i32, month: u32, day: u32) -> NaiveDate {
    let mut d = NaiveDate::from_ymd_opt(year, month, day).expect("valid");
    while d.weekday() != Weekday::Sun {
        d += Duration::days(1);
    }
    d
}

#[cfg(test)]
mod tests {
    use super::*;

    fn at(s: &str) -> NaiveDateTime {
        NaiveDateTime::parse_from_str(s, "%Y-%m-%d %H:%M:%S").unwrap()
    }

    #[test]
    fn transitions_match_zoneinfo_2025_2028() {
        // (zone, first instant of the new offset, offset after) from zoneinfo.
        let cases = [
            (Zone::Santiago, "2026-04-05 03:00:00", -4), (Zone::Santiago, "2026-09-06 04:00:00", -3),
            (Zone::Santiago, "2028-04-02 03:00:00", -4), (Zone::Santiago, "2028-09-03 04:00:00", -3),
            (Zone::Madrid, "2026-03-29 01:00:00", 2), (Zone::Madrid, "2026-10-25 01:00:00", 1),
            (Zone::Madrid, "2027-10-31 01:00:00", 1),
            (Zone::NewYork, "2026-03-08 07:00:00", -4), (Zone::NewYork, "2026-11-01 06:00:00", -5),
            (Zone::NewYork, "2027-03-14 07:00:00", -4), (Zone::NewYork, "2028-11-05 06:00:00", -5),
        ];
        for (z, t, after) in cases {
            let t = at(t);
            assert_eq!(z.offset_at_utc(t), after * 3600, "{z:?} {t}");
            assert_ne!(z.offset_at_utc(t - Duration::seconds(1)), after * 3600, "{z:?} {t}");
        }
    }

    /// Every offset change zoneinfo reports for 2025-2075 (sampled every 30
    /// minutes), from a JSON list of `[zone, utc, offset_after]` named by
    /// `TZ_TRANSITIONS`. Checks the offset at each change, just before it,
    /// and in the middle of every interval between two changes.
    #[test]
    #[ignore]
    fn zones_match_zoneinfo() {
        let path = std::env::var("TZ_TRANSITIONS").expect("set TZ_TRANSITIONS");
        let rows: Vec<(String, String, i32)> = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let mut checked = 0;
        for (i, (name, t, off)) in rows.iter().enumerate() {
            let z = Zone::from_name(name).unwrap();
            let t = at(t);
            assert_eq!(z.offset_at_utc(t), *off, "{name} at {t}");
            checked += 1;
            if let Some((next_name, next_t, _)) = rows.get(i + 1).filter(|r| &r.0 == name) {
                let _ = next_name;
                let next_t = at(next_t);
                assert_eq!(z.offset_at_utc(next_t - Duration::minutes(30)), *off, "{name} before {next_t}");
                assert_eq!(z.offset_at_utc(t + (next_t - t) / 2), *off, "{name} between {t} and {next_t}");
                checked += 2;
            } else {
                let end = at("2076-01-01 00:00:00");
                assert_eq!(z.offset_at_utc(end), *off, "{name} to the end");
            }
        }
        assert!(checked > 300);
    }

    #[test]
    fn gaps_and_folds() {
        let ny = Zone::NewYork;
        assert!(!ny.exists(at("2026-03-08 02:30:00")));
        assert!(ny.exists(at("2026-03-08 03:00:00")));
        assert_eq!(ny.local_offset(at("2026-11-01 01:30:00"), 0), -4 * 3600);
        assert_eq!(ny.local_offset(at("2026-11-01 01:30:00"), 1), -5 * 3600);
        let scl = Zone::Santiago;
        assert!(!scl.exists(at("2026-09-06 00:30:00")));
        assert_eq!(scl.local_offset(at("2026-04-04 23:30:00"), 0), -3 * 3600);
        assert_eq!(scl.local_offset(at("2026-04-04 23:30:00"), 1), -4 * 3600);
    }

    #[test]
    fn unsupported_names_are_refused() {
        assert!(Zone::from_name("Mars/Olympus").is_none());
        assert_eq!(Zone::from_name(DEFAULT_TZ), Some(Zone::Fixed(-6 * 3600)));
    }
}
