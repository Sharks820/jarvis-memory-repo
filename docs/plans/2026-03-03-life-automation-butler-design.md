# Life Automation Butler System — Design Document

**Date:** 2026-03-03
**Milestone:** v5.0 — Life Automation Butler
**Scope:** 20 features across 4 domains: Financial Intelligence, Health & Habits, Time/Schedule/Location, Communication & News

## Core Philosophy

**"Jarvis should be a filter, not a firehose."**

Every feature routes through the existing anti-spam stack:
- 4-tier notification channels (URGENT/IMPORTANT/ROUTINE/BACKGROUND)
- NotificationLearner (auto-demotes ignored notification types over 30-day rolling window)
- NudgeResponseTracker (80% ignore rate over 20 samples = auto-suppresses)
- Context filtering (only URGENT during MEETING/DRIVING/SLEEPING)
- **New: Global daily notification budget** — max 8 proactive notifications/day across ALL new features (breaking news exempt since user explicitly wants those)

Butler principle: "Sir, X needs your attention" with one-tap action buttons. Jarvis decides what matters based on learned behavior, not static rules.

## Autonomy Model

**Suggest + one-tap action.** Jarvis surfaces the right information at the right time with an actionable button. User taps to confirm. No auto-actions without confirmation.

## Data Collection Model

**Max passive.** Zero manual entry. Jarvis learns from:
- Bank/P2P app notifications (already intercepted via NotificationListenerService)
- Conversations and voice commands (already ingested via KG pipeline)
- Phone sensors (accelerometer, barometer, light, step counter — already partially used)
- Screen usage patterns (UsageStatsManager — new permission needed)
- Calendar events (already accessed via CalendarContract)
- GPS location (already available via foreground service)

---

## Domain 1: Financial Intelligence

### Feature 1: Merchant Name Normalizer

**Purpose:** Resolve merchant name variations into canonical names. Enables accurate per-merchant rollups and recurring charge detection.

**Architecture:**
- New `MerchantNormalizer` singleton in `android/.../feature/finance/`
- Three-tier normalization:
  1. Static mapping table (~50 common aliases: "AMZN*" → "Amazon", "SQ *" → Square prefix strip, "APL*" → Apple, etc.)
  2. Prefix stripping: remove known billing prefixes (SQ *, TST*, PP*, PAYPAL *, CKE*, APL*)
  3. Fuzzy matching: Jaro-Winkler similarity > 0.85 clusters remaining merchants (simple edit-distance on Android, no external dependency)
- Applied at parse time in `BankNotificationParser.parseAndStore()`
- New field on `TransactionEntity`: `normalizedMerchant: String`

**Room migration:** v11 → v12 (bundled with all financial entity changes)

### Feature 2: P2P Payment Tracker

**Purpose:** Extend notification parsing to Venmo, PayPal, Cash App, Zelle, Google Pay.

**Architecture:**
- Expand `BANK_PACKAGES` in `JarvisNotificationListenerService`:
  - `com.venmo`, `com.paypal.android.p2pmobile`, `com.squareup.cash`, `com.google.android.apps.nbu.paisa.user`
- Add P2P regex patterns to `BankNotificationParser`:
  - Venmo: `(\w[\w\s]+?) paid you \$([\d,]+\.\d{2})` and reverse
  - Cash App: `You sent \$([\d,]+\.\d{2}) to (.+)` and reverse
  - Zelle: `received \$([\d,]+\.\d{2}) from (.+)` (embedded in bank app notifications)
- New fields on `TransactionEntity`: `direction: String` ("debit"/"credit"), `counterparty: String?`
- Privacy: counterparty stored only in SQLCipher-encrypted Room DB. Masked (first name + last initial) in desktop sync.

### Feature 3: Income Cycle Detector

**Purpose:** Detect paycheck deposits and learn pay schedule. Enables downstream cash flow features.

**Architecture:**
- New `IncomeCycleDetector` class in `android/.../feature/finance/`
- Income detection: parse "deposit", "direct deposit", "credit", "received" from bank notifications
- Cycle learning: group credit transactions by approximate amount (±5%) and day-of-month clusters. 2+ occurrences at regular intervals = detected cycle.
- New Room entity: `RecurringPatternEntity(id, merchant, normalizedAmount, period, direction, counterparty, lastSeen, firstSeen, isActive, isGhost)`
  - `period`: enum of WEEKLY/BIWEEKLY/MONTHLY/QUARTERLY/ANNUAL
- Sync to desktop KG as facts: `(owner, income_cycle, "biweekly ~$2,150 on Fridays")`
- Proactive alert on income arrival: ROUTINE — "Sir, your paycheck ($X) arrived. Next expected [date]."

### Feature 4: Spending Velocity Monitor

**Purpose:** Track rolling spending rate by category and alert when pace exceeds historical norms.

**Architecture:**
- New `SpendingVelocityMonitor` class, triggered daily from `SyncWorker`
- Queries `TransactionDao`:
  - Current 7-day total by category
  - Rolling 30-day weekly average by category (sum / 4.3)
  - Current 7-day total overall
- Alert threshold: current week >= 1.8x the 30-day weekly average for any category OR overall
- Routes through `NudgeEngine` with `NotificationPriority.IMPORTANT`
- Butler message: "Sir, you've spent $340 on dining this week. Your typical weekly dining spend is $148. You're on pace for $486 by Sunday."
- NotificationLearner integration: if user consistently dismisses dining velocity alerts, they auto-demote to ROUTINE

### Feature 5: Recurring Charge Discoverer

**Purpose:** Auto-discover subscriptions from transaction patterns. Detect subscription creep and ghost subscriptions.

**Architecture:**
- New `RecurringChargeDiscoverer` class, runs weekly from `SyncWorker`
- Algorithm:
  1. Query all transactions from last 90 days
  2. Group by `normalizedMerchant`
  3. For each group, compute inter-transaction intervals
  4. If 2+ transactions have consistent intervals (±3 days monthly, ±1 day weekly): classify as recurring
  5. Store in `RecurringPatternEntity`
- **Ghost detection:** Cross-reference subscription merchants against `PackageManager.getInstalledApplications()`. Charge exists but app not installed = ghost subscription.
- **Creep detection:** Compare total monthly recurring charges vs 3 months ago. If increased by > $20/month, alert with list of new charges.
- Sync discovered recurring charges to desktop `OpsSnapshot.subscriptions` for daily brief inclusion

### Feature 6: Pre-Bill Awareness Alerts

**Purpose:** Alert 2 days before expected recurring charges with price-change context.

**Architecture:**
- New `PreBillAlertWorker`, daily check from `SyncWorker`
- For each active `RecurringPatternEntity`, calculate `expected_next = lastSeen + period`
- If within 2 days: fire via `NudgeEngine`
- Include price change context: if last charge differed from historical average, mention it
- Priority: ROUTINE by default, IMPORTANT if charge > $50 or price change detected
- Butler message: "Sir, Netflix ($15.99) will likely charge in 2 days. It went up $2 last month."

---

## Domain 2: Health & Habits

### Feature 7: Sleep Quality Estimator

**Purpose:** Estimate sleep onset, wake time, duration, and quality from phone behavior. Correlate with daytime patterns.

**Architecture:**
- New `SleepEstimator` class in `android/.../feature/health/`
- Signals:
  - `ACTION_SCREEN_OFF/ON` broadcasts via `BroadcastReceiver` — last screen-off before long gap = sleep onset, first screen-on after gap = wake time
  - Accelerometer during charging (phone on nightstand): micro-movements indicate restlessness. Reuse existing `Handler(Looper.getMainLooper())` pattern.
  - `Sensor.TYPE_LIGHT`: ambient lux during sleep window. Spike from 0 to 200+ = bathroom trip.
  - Notification density during sleep window via existing `NotificationListenerService`
  - Alarm app notification interception: detect snooze cycles
- Quality score formula:
  ```
  quality = base_score
    - (screen_on_interruptions * 5)
    - (restless_periods * 3)
    - (late_onset_penalty)
    + (consistency_bonus)  # onset/wake within 30min of 7-day avg
    - (snooze_penalty * 2)
  ```
- New Room entity: `SleepSessionEntity(id, onsetTime, wakeTime, durationMinutes, qualityScore, interruptions, date)`
- KG sync: 7/14/30-day rolling averages for onset, duration, quality
- Cross-branch edges linking sleep sessions to preceding evening screen time sessions

### Feature 8: Screen Time Intelligence

**Purpose:** Correlate screen usage with outcomes (sleep, productivity) — not just "you used 4 hours."

**Architecture:**
- New `ScreenTimeAnalyzer` class in `android/.../feature/health/`
- **New permission required:** `PACKAGE_USAGE_STATS` (one-time user grant in Settings)
- Uses `UsageStatsManager.queryEvents()` for per-app foreground time with timestamps
- App categorization via static package-to-category map:
  - Social: com.twitter.*, com.instagram.*, com.reddit.*, com.facebook.*
  - Productivity: email, calendar, docs
  - Entertainment: streaming, games
  - Communication: messaging, calls
- Time bucketing: Morning/Afternoon/Evening/LateNight (relative to learned sleep schedule)
- **Correlation engine:** Weekly analysis correlating late-night social media minutes with sleep quality score (Pearson r). If r < -0.5, surface insight in weekly briefing.
- Insights delivered ONLY in weekly health briefing (Feature 11), never as standalone notifications
- Example: "Your 3 best sleep nights had <15 min screen time after 10pm. Your 3 worst averaged 84 min."

### Feature 9: Sedentary Break Nudges

**Purpose:** Context-aware movement reminders during prolonged stillness.

**Architecture:**
- New `SedentaryDetector` class in `android/.../feature/health/`
- Signals:
  - `Sensor.TYPE_STEP_COUNTER` (hardware, battery-efficient) — track steps-per-hour
  - Accelerometer variance over 30-second windows (variance < 0.05 m/s^2 = still)
  - Context state from existing `ContextDetector`
- Nudge logic:
  ```
  if context == NORMAL
     AND still_duration > personal_threshold  # starts at 90min, adapts
     AND time_since_last_nudge > 45min
     AND nudge_response_rate > 0.2:  # 80% ignore = suppress
       issue_movement_nudge()
  ```
- NEVER nudge during MEETING, DRIVING, or SLEEPING
- ROUTINE channel only. Never URGENT for movement.
- Butler message: "Sir, you've been at your desk for 2 hours. A short walk resets focus."

### Feature 10: Habit Streak Detection

**Purpose:** Passively detect repeated behaviors and surface them as recognized habits.

**Architecture:**
- New `HabitDetector` class in `android/.../feature/health/`
- Signals: step counter + time-of-day, app usage patterns, charging patterns, context transitions
- Detection algorithm:
  - Group events by type and 30-minute time-of-day bucket
  - A "habit" = same behavior in same bucket on >= 70% of applicable days over 14-day window
  - Track streak length (consecutive days)
- New Room entity: `HabitEntity(id, behaviorType, timeWindow, streakLength, strength, firstDetected, lastObserved)`
- Streak break nudge: only if habit active for 7+ days AND user has responded positively to habit nudges before
- Butler message: "Sir, morning walk streak: 16 days. Nice consistency." (ROUTINE)
- Also detects negative habits: "You've opened Instagram within 5 minutes of waking every day this week." (weekly briefing only, never standalone notification)

### Feature 11: Weekly Health Briefing

**Purpose:** Monday morning synthesis of all health signals into one narrative.

**Architecture:**
- Integrated into existing `MorningBriefing.kt` as a weekly section (Monday only)
- Desktop endpoint: `GET /health-summary` aggregates KG health facts
- Data: sleep averages/trends, daily steps, sedentary blocks, screen time by category, active streaks, correlations
- LLM summarization via Kimi K2: natural language narrative, not raw data
- Example output:
  ```
  WEEKLY HEALTH (Feb 24 - Mar 2):
  Sleep: 6.8hr avg (down from 7.2). Onset drifted 35min later.
  Movement: 6,400 avg daily steps (up 12%). Walk streak: 16 days.
  Screen: 5.1hr avg (down 8%). Late-night usage down 22%.
  Correlation: Your best sleep nights had <15min screen after 10pm.
  ```

---

## Domain 3: Time, Schedule & Location

### Feature 12: Proximity Errand Butler

**Purpose:** Alert when near a store where you have pending errands. Errand list populated passively.

**Architecture:**

**Errand population (passive):**
- Desktop `LLMExtractor`: new category `"errand"` with relationships `needs_to_buy`, `needs_to_pick_up`, `needs_to_return`. "I need to grab dog food" → KG errand fact.
- Android `ErrandCueExtractor` (new, similar to `SchedulingCueExtractor`): detects errand patterns from notifications — CVS "prescription ready", Amazon "delivered to locker", grocery app notifications.
- Explicit voice: "Jarvis, add milk to my Costco list" creates direct KG errand fact.
- New Room entity: `ErrandEntity(id, item, store, storeLatitude, storeLongitude, source, confidence, createdAt, completedAt, snoozedUntil)`

**Proximity trigger:**
- `ErrandProximityChecker` in `SyncWorker`, runs every 2 minutes
- Compares current GPS against `StoreLocationCache` (populated from `CommuteLocationEntity` + errand store locations)
- Haversine distance < 300m triggers check for uncompleted errands at that store
- Routes through `ProactiveAlertReceiver` pipeline with context filtering
- Per-store cooldown: 6 hours
- Butler message: "Sir, you're near Costco. You mentioned needing paper towels and dog food. Want to see your list?"
- Action buttons: "View List" / "Not Now"

### Feature 13: Departure Oracle

**Purpose:** Calendar-aware, traffic-powered departure nudges.

**Architecture:**
- Upgrade existing `TrafficChecker` to `DepartureOracle` class
- Desktop endpoint: `GET /travel-time?origin=lat,lon&dest=lat,lon` using web search for traffic estimation or OSRM for routing
- Calendar scan: query `CalendarContract.Instances` for next event with non-empty `EVENT_LOCATION`. Geocode via desktop LLM.
- Back-calculate departure time: `event_start - travel_time - buffer(15min)`
- Only nudge if:
  - Departure time is within 30 minutes
  - Today's travel estimate is > 10 minutes worse than 7-day average (avoid constant "time to leave" spam when traffic is normal)
- IMPORTANT notification with "Set Alarm" action button
- Butler message: "Sir, your 2pm dentist is 23 min away. Traffic is heavier than usual — leave by 1:25. Set alarm?"
- Commute logging: new `CommuteLogEntity(id, date, origin, destination, durationMinutes)` for anomaly detection

### Feature 14: Schedule Guardian

**Purpose:** Detect calendar conflicts at notification level, before user opens calendar.

**Architecture:**
- New `ConflictDetector` class injected alongside `CalendarEventCreator`
- Before inserting auto-detected events (from `SchedulingCueExtractor`), query `CalendarContract.Instances` for overlap
- If conflict found: IMPORTANT notification with conflict details
- Smart suggestions: scan same week for gaps >= event duration + 30min buffer. Rank by proximity to original time, time-of-day preference (from KG), travel feasibility.
- Butler message: "Sir, the dentist appointment Thursday at 2pm conflicts with your weekly standup. Want alternatives?"
- Action buttons: "See Alternatives" / "Keep Both"

### Feature 15: Enhanced Parking Valet

**Purpose:** Upgrade existing `ParkingMemory` with floor detection, meter tracking, and navigation.

**Architecture:**
- Extend existing `ParkingMemory.kt`:
  - After Bluetooth disconnect, 2-minute barometer sampling window. Pressure delta of ~0.4 hPa per floor = floor count inference.
  - New fields: `floor: Int?`, `meterExpiresAt: Long?`, `note: String?`
- Meter tracking:
  - "Add Timer" action button on parking notification (30min/1hr/2hr/Custom)
  - Passive detection: if `NotificationListenerService` sees ParkMobile/SpotHero notification, extract expiry time
  - 10 minutes before expiry: URGENT notification — "Sir, parking meter expires in 10 minutes."
- "Navigate" action button: launches Google Maps with `google.navigation:q=lat,lon&mode=w` (walking)
- Voice augmentation: "Jarvis, I parked in Row B" → note stored via KG sync

### Feature 16: Late-Running Courtesy

**Purpose:** Detect lateness and offer to text attendees your ETA.

**Architecture:**
- New `LateDetector` class in `SyncWorker`, runs every 2 minutes
- For each upcoming calendar event with attendees:
  - Compute required departure = event_start - travel_time
  - If current_time > required_departure AND context != DRIVING: still at origin, departing late
  - If context == DRIVING AND estimated_arrival > event_start: en route but late
- Attendee lookup: `CalendarContract.Attendees` → email → device Contacts → phone number
- IMPORTANT notification with "Send ETA" action button
- Tapping launches `Intent.ACTION_SENDTO` with pre-composed message (user reviews before sending)
- Anti-spam: once per event, only when delay > 5 minutes, only for events with attendees
- Butler message: "Sir, you're running 12 min late for lunch with Mike. Send him your ETA?"

---

## Domain 4: Communication & News

### Feature 17: Promise Tracker

**Purpose:** Detect commitments in conversations and track to completion.

**Architecture:**
- Desktop: new `engine/src/jarvis_engine/commitments/` module
  - `CommitmentExtractor`: regex + LLM hybrid. Patterns: "I'll", "I will", "let me", "I need to send", "by [day/date]"
  - New `commitment_tracking` SQLite table: `id, source_contact, direction (inbound/outbound), commitment_text, inferred_deadline, status, created_at, resolved_at, source_memory_id`
  - Deadline inference: dateutil-style parsing for "by Friday", "tomorrow", "next week". Default 72 hours if no deadline stated.
  - New trigger: `check_commitment_deadlines()` — fires when outbound commitment is within 4 hours of deadline, or inbound commitment is 24 hours past deadline
- Android: new `CommitmentDetector.kt` — sends conversation text to new `/commitment-scan` endpoint
- IMPORTANT notification with "Mark Done" / "Snooze 24h" action buttons
- Butler message: "Sir, you told Sarah you'd send the proposal. That was 2 days ago."
- KG edges: `contact -> has_open_commitment -> commitment_node`

### Feature 18: Unanswered Message Detector

**Purpose:** Importance-weighted reminders for unreplied messages.

**Architecture:**
- Android-only: new `UnansweredMessageDetector.kt` in `feature/communication/`
- New Room entity: `PendingReplyEntity(id, contactName, phoneNumber, packageName, messagePreview, receivedAt, importance, resolved)`
- Extended `JarvisNotificationListenerService`: insert `PendingReplyEntity` when message arrives from WhatsApp, Signal, Telegram, SMS
- Resolution detection: `ContentObserver` on `content://sms/sent` + notification interception for other apps
- Reminder threshold scaled by `ContactContextEntity.importance`:
  - importance 1.0 → remind after 1 hour
  - importance 0.5 → remind after 4 hours
  - importance 0.2 → remind after 8 hours
  - Below 0.2 → no reminder
- Only tracks contacts already in `contact_context` table. Unknown numbers ignored.
- ROUTINE channel. Action buttons: "Reply" (opens messaging app) / "Dismiss"
- Butler message: "Sir, your mom texted 3 hours ago. You haven't replied."

### Feature 19: Morning News Digest

**Purpose:** Personalized daily headlines alongside existing morning briefing.

**Architecture:**
- Desktop: new `engine/src/jarvis_engine/news/` module
  - `NewsAggregator`: uses existing `search_web()` for "top world news today" + interest-specific queries
  - `InterestLearner`: builds topic profile from KG facts, conversation topics, search history. Exponential decay (30-day half-life).
  - `NewsDeduplicator`: URL dedup → Jaro-Winkler headline similarity > 0.85 → semantic dedup via sqlite-vec embeddings > 0.80
  - `news_cache` SQLite table: `id, headline, source_url, source_domain, topic, summary, importance_score, published_date, cluster_id`
  - LLM summarization: Kimi K2 summarizes each story in one sentence, ranks by global importance, returns top 7
  - Source diversity: max 2 headlines per domain
  - Clickbait filter: LLM scoring + regex for known patterns
- Integration: extends `MorningBriefing.kt` `onWakeUp()` — news digest appended to existing briefing
- Local news section: reverse-geocode user location, query "[city] local news today", 1-3 local items
- Delivered as part of existing morning briefing notification — NO separate notification
- Example: "WORLD: [5-7 headlines]. LOCAL: [1-2 items]."

### Feature 20: Breaking News Alerts

**Purpose:** Push notifications for genuinely major world events only.

**Architecture:**
- Desktop: new `BreakingNewsMonitor` in `engine/src/jarvis_engine/news/breaking.py`
- Runs every 15 minutes in daemon loop
- Multi-source corroboration: story must appear in 3+ different domains to be considered "breaking"
- LLM severity scoring (Kimi K2):
  - 10 = nuclear event, major war, pandemic
  - 8-9 = major natural disaster (100+ dead), financial crash (>5%)
  - 6-7 = significant political event, major election result
  - 5 or below = routine news
  - Only alerts scoring >= 7 are delivered
- Rate limiting: max 1 alert per 6 hours, max 3 per 24 hours
- `breaking_news_alerts` SQLite table with story hash for 24-hour dedup
- URGENT channel (bypasses DND — user explicitly wants this)
- NotificationLearner integration: if user consistently dismisses, severity threshold auto-increases to >= 8
- Butler message: "BREAKING: [one-line summary]. Want details?"
- Tapping opens Jarvis chat for on-demand web research via existing `run_web_research()`

---

## Technical Architecture

### Room Database Migration (v11 → v12)

New/modified entities:
- `TransactionEntity`: add `direction`, `normalizedMerchant`, `counterparty`
- `RecurringPatternEntity`: new table
- `SleepSessionEntity`: new table
- `HabitEntity`: new table
- `ErrandEntity`: new table
- `PendingReplyEntity`: new table
- `CommuteLogEntity`: new table
- `ParkingEntity`: add `floor`, `meterExpiresAt`, `note`

### New Android Permissions
- `PACKAGE_USAGE_STATS` (one-time user grant in Settings for screen time intelligence)
- No other new permissions needed — all other signals use existing permissions

### New Desktop Engine Modules
- `engine/src/jarvis_engine/commitments/` — commitment extraction and tracking
- `engine/src/jarvis_engine/news/` — news aggregation, breaking monitor, dedup, interest learning

### New Desktop API Endpoints
- `GET /travel-time?origin=lat,lon&dest=lat,lon` — traffic-aware travel time
- `GET /health-summary` — weekly health data aggregation
- `POST /commitment-scan` — extract commitments from conversation text
- `GET /news-digest` — morning news digest
- `GET /breaking-check` — breaking news status (called by daemon, not phone)
- `POST /errand` — create/complete errands
- `GET /errands?store=X` — list errands for a store

### Anti-Spam Architecture (Global)

All 20 features share the same pipeline:

1. **4-tier channels:** URGENT (bypasses DND), IMPORTANT (vibrates), ROUTINE (silent), BACKGROUND (invisible)
2. **NotificationLearner:** auto-demotes per feature type based on 30-day act/dismiss ratio
3. **NudgeResponseTracker:** 80% ignore rate over 20 samples = complete suppression
4. **Context filter:** only URGENT during MEETING/DRIVING/SLEEPING. All others held for ContextDigest.
5. **NotificationBatcher:** groups 3+ related alerts into single InboxStyle notification
6. **Global daily budget:** 8 proactive notifications/day max across all new features (breaking news exempt). Features compete for budget slots based on learned engagement priority.
7. **Per-feature cooldowns:** errand proximity (6hr/store), departure oracle (once/event), sedentary (45min), pre-bill (once/charge), breaking news (6hr), unanswered messages (once/contact/day)

### Phase Plan

**Phase A: Foundation** — entities, normalizer, income/P2P, sleep, screen time, interest learner
**Phase B: Intelligence Layer** — spending velocity, recurring charges, promises, unanswered messages, departure oracle, schedule guardian
**Phase C: Butler Features** — pre-bill alerts, errand butler, sedentary nudges, habit streaks, parking valet, late-running courtesy, news digest, breaking news, weekly briefings
**Phase D: Polish** — global notification budget, cross-signal correlations, notification learning tuning

---

## Success Criteria

- All 20 features operational with zero manual data entry required
- Global notification budget enforced (max 8/day excluding breaking news)
- NotificationLearner auto-tunes all new feature types within 2 weeks of use
- Breaking news fires 0-3 times per week (not per day)
- All new features have toggle switches in Settings screen
- Room migration v11 → v12 preserves all existing data
- All existing 4366 tests continue to pass
- New features covered by dedicated tests
