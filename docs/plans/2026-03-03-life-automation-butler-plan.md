# v5.0 Life Automation Butler — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement 20 proactive life-automation features across financial intelligence, health/habits, time/schedule/location, and communication/news — all zero-manual-entry, butler-style, anti-spam.

**Architecture:** Android features use `@Singleton` + `@Inject constructor`, integrate with existing `SyncWorker` for periodic checks, route notifications through `NotificationChannelManager` 4-tier channels + `NudgeResponseTracker` adaptive suppression. Desktop features use CQRS command/handler pattern, register in `app.py`, add mobile API endpoints via `_GET_DISPATCH`/`_POST_DISPATCH` dicts. Room DB migrates from v11 → v12 with explicit `MIGRATION_11_12`.

**Tech Stack:** Kotlin (Android/Compose/Room/Hilt), Python (desktop engine, stdlib HTTP server, SQLite), existing Jarvis infrastructure.

**Design Doc:** `docs/plans/2026-03-03-life-automation-butler-design.md`

---

## Phase A: Foundation

Phase A builds the data layer and passive detection systems that all other features depend on.

### Task A1: Room Database Migration v11 → v12

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/assistant/data/JarvisDatabase.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/data/entity/TransactionEntity.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/data/entity/ParkingEntity.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/entity/RecurringPatternEntity.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/entity/SleepSessionEntity.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/entity/ErrandEntity.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/entity/PendingReplyEntity.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/entity/CommuteLogEntity.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/dao/RecurringPatternDao.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/dao/SleepSessionDao.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/dao/ErrandDao.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/dao/PendingReplyDao.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/data/dao/CommuteLogDao.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/di/AppModule.kt`

**Step 1: Add new fields to TransactionEntity**

Add three new fields to `TransactionEntity.kt`:
```kotlin
val direction: String = "debit",        // "debit" or "credit"
val normalizedMerchant: String = "",    // cleaned merchant name
val counterparty: String = "",          // for P2P transactions
```

**Step 2: Add new fields to ParkingEntity**

Add three new fields to `ParkingEntity.kt`:
```kotlin
val floor: Int? = null,
val meterExpiresAt: Long? = null,
val meterNote: String = "",
```

**Step 3: Create new Room entities**

Create `RecurringPatternEntity.kt`:
```kotlin
@Entity(tableName = "recurring_patterns")
data class RecurringPatternEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val merchant: String,
    val normalizedAmount: Double,
    val period: String,              // "WEEKLY","BIWEEKLY","MONTHLY","QUARTERLY","ANNUAL"
    val direction: String = "debit", // "debit" or "credit"
    val counterparty: String = "",
    val lastSeen: String,            // yyyy-MM-dd
    val firstSeen: String,           // yyyy-MM-dd
    val isActive: Boolean = true,
    val isGhost: Boolean = false,
    val occurrenceCount: Int = 2,
    val createdAt: Long = System.currentTimeMillis(),
)
```

Create `SleepSessionEntity.kt`:
```kotlin
@Entity(tableName = "sleep_sessions")
data class SleepSessionEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val onsetTime: Long,             // epoch millis
    val wakeTime: Long,              // epoch millis
    val durationMinutes: Int,
    val qualityScore: Int,           // 0-100
    val interruptions: Int = 0,
    val restlessPeriods: Int = 0,
    val date: String,                // yyyy-MM-dd of wake date
    val createdAt: Long = System.currentTimeMillis(),
)
```

Create `ErrandEntity.kt`:
```kotlin
@Entity(tableName = "errands")
data class ErrandEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val item: String,
    val store: String = "",
    val storeLatitude: Double? = null,
    val storeLongitude: Double? = null,
    val source: String = "conversation",  // "conversation","notification","voice"
    val confidence: Float = 0.8f,
    val createdAt: Long = System.currentTimeMillis(),
    val completedAt: Long? = null,
    val snoozedUntil: Long? = null,
)
```

Create `PendingReplyEntity.kt`:
```kotlin
@Entity(tableName = "pending_replies")
data class PendingReplyEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val contactName: String,
    val phoneNumber: String = "",
    val packageName: String,
    val messagePreview: String,
    val receivedAt: Long = System.currentTimeMillis(),
    val importance: Float = 0.5f,
    val reminderSentAt: Long? = null,
    val resolved: Boolean = false,
)
```

Create `CommuteLogEntity.kt`:
```kotlin
@Entity(tableName = "commute_log")
data class CommuteLogEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val date: String,                // yyyy-MM-dd
    val originLabel: String = "",
    val destinationLabel: String = "",
    val durationMinutes: Int,
    val distanceKm: Float = 0f,
    val timestamp: Long = System.currentTimeMillis(),
)
```

**Step 4: Create DAOs**

Create `RecurringPatternDao.kt`:
```kotlin
@Dao
interface RecurringPatternDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(pattern: RecurringPatternEntity): Long

    @Query("SELECT * FROM recurring_patterns WHERE isActive = 1 ORDER BY lastSeen DESC")
    suspend fun getActive(): List<RecurringPatternEntity>

    @Query("SELECT * FROM recurring_patterns WHERE merchant = :merchant AND direction = :direction LIMIT 1")
    suspend fun findByMerchant(merchant: String, direction: String = "debit"): RecurringPatternEntity?

    @Query("SELECT SUM(normalizedAmount) FROM recurring_patterns WHERE isActive = 1 AND direction = 'debit' AND period = 'MONTHLY'")
    suspend fun getMonthlySubscriptionTotal(): Double?

    @Update
    suspend fun update(pattern: RecurringPatternEntity)
}
```

Create `SleepSessionDao.kt`:
```kotlin
@Dao
interface SleepSessionDao {
    @Insert
    suspend fun insert(session: SleepSessionEntity): Long

    @Query("SELECT * FROM sleep_sessions WHERE date BETWEEN :startDate AND :endDate ORDER BY date DESC")
    suspend fun getInRange(startDate: String, endDate: String): List<SleepSessionEntity>

    @Query("SELECT AVG(durationMinutes) FROM sleep_sessions WHERE date >= :sinceDate")
    suspend fun getAvgDuration(sinceDate: String): Double?

    @Query("SELECT AVG(qualityScore) FROM sleep_sessions WHERE date >= :sinceDate")
    suspend fun getAvgQuality(sinceDate: String): Double?

    @Query("SELECT * FROM sleep_sessions ORDER BY date DESC LIMIT 1")
    suspend fun getLatest(): SleepSessionEntity?
}
```

Create `ErrandDao.kt`:
```kotlin
@Dao
interface ErrandDao {
    @Insert
    suspend fun insert(errand: ErrandEntity): Long

    @Query("SELECT * FROM errands WHERE completedAt IS NULL AND (snoozedUntil IS NULL OR snoozedUntil < :nowMs) ORDER BY createdAt DESC")
    suspend fun getPending(nowMs: Long = System.currentTimeMillis()): List<ErrandEntity>

    @Query("SELECT * FROM errands WHERE completedAt IS NULL AND store = :store")
    suspend fun getPendingForStore(store: String): List<ErrandEntity>

    @Query("UPDATE errands SET completedAt = :nowMs WHERE id = :id")
    suspend fun markCompleted(id: Long, nowMs: Long = System.currentTimeMillis())

    @Query("UPDATE errands SET snoozedUntil = :until WHERE id = :id")
    suspend fun snooze(id: Long, until: Long)
}
```

Create `PendingReplyDao.kt`:
```kotlin
@Dao
interface PendingReplyDao {
    @Insert
    suspend fun insert(reply: PendingReplyEntity): Long

    @Query("SELECT * FROM pending_replies WHERE resolved = 0 ORDER BY receivedAt DESC")
    suspend fun getUnresolved(): List<PendingReplyEntity>

    @Query("UPDATE pending_replies SET resolved = 1 WHERE id = :id")
    suspend fun markResolved(id: Long)

    @Query("UPDATE pending_replies SET resolved = 1 WHERE contactName = :contactName AND packageName = :packageName")
    suspend fun resolveByContact(contactName: String, packageName: String)

    @Query("DELETE FROM pending_replies WHERE receivedAt < :beforeMs")
    suspend fun deleteOlderThan(beforeMs: Long)
}
```

Create `CommuteLogDao.kt`:
```kotlin
@Dao
interface CommuteLogDao {
    @Insert
    suspend fun insert(log: CommuteLogEntity): Long

    @Query("SELECT AVG(durationMinutes) FROM commute_log WHERE date >= :sinceDate")
    suspend fun getAvgDuration(sinceDate: String): Double?

    @Query("SELECT * FROM commute_log WHERE date BETWEEN :startDate AND :endDate ORDER BY date DESC")
    suspend fun getInRange(startDate: String, endDate: String): List<CommuteLogEntity>
}
```

**Step 5: Write MIGRATION_11_12**

In `JarvisDatabase.kt`, add migration object:
```kotlin
private val MIGRATION_11_12 = object : Migration(11, 12) {
    override fun migrate(db: SupportSQLiteDatabase) {
        // TransactionEntity new columns
        db.execSQL("ALTER TABLE transactions ADD COLUMN direction TEXT NOT NULL DEFAULT 'debit'")
        db.execSQL("ALTER TABLE transactions ADD COLUMN normalizedMerchant TEXT NOT NULL DEFAULT ''")
        db.execSQL("ALTER TABLE transactions ADD COLUMN counterparty TEXT NOT NULL DEFAULT ''")

        // ParkingEntity new columns
        db.execSQL("ALTER TABLE parking_locations ADD COLUMN floor INTEGER")
        db.execSQL("ALTER TABLE parking_locations ADD COLUMN meterExpiresAt INTEGER")
        db.execSQL("ALTER TABLE parking_locations ADD COLUMN meterNote TEXT NOT NULL DEFAULT ''")

        // New tables
        db.execSQL("""CREATE TABLE IF NOT EXISTS recurring_patterns (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            merchant TEXT NOT NULL,
            normalizedAmount REAL NOT NULL,
            period TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'debit',
            counterparty TEXT NOT NULL DEFAULT '',
            lastSeen TEXT NOT NULL,
            firstSeen TEXT NOT NULL,
            isActive INTEGER NOT NULL DEFAULT 1,
            isGhost INTEGER NOT NULL DEFAULT 0,
            occurrenceCount INTEGER NOT NULL DEFAULT 2,
            createdAt INTEGER NOT NULL DEFAULT 0
        )""")
        db.execSQL("""CREATE TABLE IF NOT EXISTS sleep_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            onsetTime INTEGER NOT NULL,
            wakeTime INTEGER NOT NULL,
            durationMinutes INTEGER NOT NULL,
            qualityScore INTEGER NOT NULL,
            interruptions INTEGER NOT NULL DEFAULT 0,
            restlessPeriods INTEGER NOT NULL DEFAULT 0,
            date TEXT NOT NULL,
            createdAt INTEGER NOT NULL DEFAULT 0
        )""")
        db.execSQL("""CREATE TABLE IF NOT EXISTS errands (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            item TEXT NOT NULL,
            store TEXT NOT NULL DEFAULT '',
            storeLatitude REAL,
            storeLongitude REAL,
            source TEXT NOT NULL DEFAULT 'conversation',
            confidence REAL NOT NULL DEFAULT 0.8,
            createdAt INTEGER NOT NULL DEFAULT 0,
            completedAt INTEGER,
            snoozedUntil INTEGER
        )""")
        db.execSQL("""CREATE TABLE IF NOT EXISTS pending_replies (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            contactName TEXT NOT NULL,
            phoneNumber TEXT NOT NULL DEFAULT '',
            packageName TEXT NOT NULL,
            messagePreview TEXT NOT NULL,
            receivedAt INTEGER NOT NULL DEFAULT 0,
            importance REAL NOT NULL DEFAULT 0.5,
            reminderSentAt INTEGER,
            resolved INTEGER NOT NULL DEFAULT 0
        )""")
        db.execSQL("""CREATE TABLE IF NOT EXISTS commute_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
            date TEXT NOT NULL,
            originLabel TEXT NOT NULL DEFAULT '',
            destinationLabel TEXT NOT NULL DEFAULT '',
            durationMinutes INTEGER NOT NULL,
            distanceKm REAL NOT NULL DEFAULT 0,
            timestamp INTEGER NOT NULL DEFAULT 0
        )""")
    }
}
```

Update `@Database` annotation to version 12, add all 5 new entities to the entities array. Add all 5 new abstract DAO accessors. Register `MIGRATION_11_12` in `.addMigrations(...)`.

**Step 6: Register DAOs in AppModule**

Add 5 new `@Provides` functions in `AppModule.kt` following existing pattern:
```kotlin
@Provides fun provideRecurringPatternDao(db: JarvisDatabase): RecurringPatternDao = db.recurringPatternDao()
@Provides fun provideSleepSessionDao(db: JarvisDatabase): SleepSessionDao = db.sleepSessionDao()
@Provides fun provideErrandDao(db: JarvisDatabase): ErrandDao = db.errandDao()
@Provides fun providePendingReplyDao(db: JarvisDatabase): PendingReplyDao = db.pendingReplyDao()
@Provides fun provideCommuteLogDao(db: JarvisDatabase): CommuteLogDao = db.commuteLogDao()
```

**Step 7: Commit**
```
feat(android): Room v11→v12 migration — 5 new tables, 6 new columns

Adds RecurringPattern, SleepSession, Errand, PendingReply, CommuteLog
entities. Extends Transaction with direction/normalizedMerchant/counterparty.
Extends Parking with floor/meterExpiresAt/meterNote.
```

---

### Task A2: Merchant Name Normalizer

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/finance/MerchantNormalizer.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/finance/BankNotificationParser.kt`

**Step 1: Create MerchantNormalizer**

```kotlin
package com.jarvis.assistant.feature.finance

import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class MerchantNormalizer @Inject constructor() {

    companion object {
        private val STATIC_MAP = mapOf(
            "amzn" to "Amazon", "amazon" to "Amazon", "amzn*mktplace" to "Amazon",
            "apple.com/bill" to "Apple", "apl*apple" to "Apple", "apple inc" to "Apple",
            "google*" to "Google", "google play" to "Google", "goog*" to "Google",
            "netflix" to "Netflix", "netflix.com" to "Netflix",
            "spotify" to "Spotify", "spotify usa" to "Spotify",
            "uber" to "Uber", "uber*trip" to "Uber", "uber*eats" to "Uber Eats",
            "lyft" to "Lyft", "lyft*ride" to "Lyft",
            "doordash" to "DoorDash", "dd doordash" to "DoorDash",
            "grubhub" to "Grubhub", "gh*grubhub" to "Grubhub",
            "starbucks" to "Starbucks", "sbux" to "Starbucks",
            "walmart" to "Walmart", "wal-mart" to "Walmart", "wm supercenter" to "Walmart",
            "target" to "Target", "target.com" to "Target",
            "costco" to "Costco", "costco whse" to "Costco",
            "walgreens" to "Walgreens", "cvs" to "CVS", "cvs/pharmacy" to "CVS",
            "shell oil" to "Shell", "chevron" to "Chevron", "exxonmobil" to "ExxonMobil",
            "venmo" to "Venmo", "paypal" to "PayPal", "cashapp" to "Cash App",
            "hulu" to "Hulu", "disney+" to "Disney+", "disneyplus" to "Disney+",
            "hbo max" to "Max", "hbomax" to "Max",
            "youtube" to "YouTube Premium", "youtubepremium" to "YouTube Premium",
            "paramount+" to "Paramount+", "peacock" to "Peacock",
        )

        private val BILLING_PREFIXES = listOf(
            "sq *", "sq*", "tst*", "tst *", "pp*", "pp *", "paypal *",
            "cke*", "apl*", "goog*", "amzn*", "dd *", "gh*",
        )
    }

    fun normalize(rawMerchant: String): String {
        val cleaned = rawMerchant.trim().lowercase()

        // 1. Static map lookup
        STATIC_MAP[cleaned]?.let { return it }

        // 2. Strip billing prefixes
        var stripped = cleaned
        for (prefix in BILLING_PREFIXES) {
            if (stripped.startsWith(prefix)) {
                stripped = stripped.removePrefix(prefix).trim()
                break
            }
        }

        // 3. Re-check static map after stripping
        STATIC_MAP[stripped]?.let { return it }

        // 4. Partial match on static map keys
        for ((key, canonical) in STATIC_MAP) {
            if (stripped.contains(key) || key.contains(stripped)) {
                return canonical
            }
        }

        // 5. Title case the stripped version
        return stripped.split(" ").joinToString(" ") { word ->
            word.replaceFirstChar { it.uppercase() }
        }
    }
}
```

**Step 2: Integrate into BankNotificationParser**

Add `MerchantNormalizer` to the constructor injection:
```kotlin
@Singleton
class BankNotificationParser @Inject constructor(
    private val transactionDao: TransactionDao,
    private val anomalyDetector: AnomalyDetector,
    private val merchantNormalizer: MerchantNormalizer,
)
```

In `parseAndStore()`, after extracting the merchant, set `normalizedMerchant`:
```kotlin
val normalized = merchantNormalizer.normalize(parsed.merchant)
val entity = TransactionEntity(
    amount = parsed.amount,
    merchant = parsed.merchant,
    normalizedMerchant = normalized,
    // ... rest of fields
)
```

**Step 3: Commit**
```
feat(android): merchant name normalizer — 50+ alias mappings, prefix stripping
```

---

### Task A3: P2P Payment Tracker

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/finance/BankNotificationParser.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/scheduling/JarvisNotificationListenerService.kt`

**Step 1: Expand BANK_PACKAGES**

Add to the `BANK_PACKAGES` set in `BankNotificationParser`:
```kotlin
"com.venmo",
"com.paypal.android.p2pmobile",
"com.squareup.cash",
"com.google.android.apps.nbu.paisa.user",
"com.zellepay.zelle",
```

**Step 2: Add P2P regex patterns and income detection**

Add new pattern lists in companion object:
```kotlin
private val P2P_RECEIVED_PATTERNS = listOf(
    Regex("""(?i)(\w[\w\s]*?)\s+paid\s+you\s+\$([\d,]+\.\d{2})"""),
    Regex("""(?i)received\s+\$([\d,]+\.\d{2})\s+from\s+(.+?)(?:\.|$)"""),
    Regex("""(?i)(\w[\w\s]*?)\s+sent\s+you\s+\$([\d,]+\.\d{2})"""),
)

private val P2P_SENT_PATTERNS = listOf(
    Regex("""(?i)you\s+paid\s+(\w[\w\s]*?)\s+\$([\d,]+\.\d{2})"""),
    Regex("""(?i)you\s+sent\s+\$([\d,]+\.\d{2})\s+to\s+(.+?)(?:\.|$)"""),
)

private val INCOME_PATTERNS = listOf(
    Regex("""(?i)(?:direct\s+)?deposit\s+(?:of\s+)?\$([\d,]+\.\d{2})"""),
    Regex("""(?i)credit\s+(?:of\s+)?\$([\d,]+\.\d{2})"""),
    Regex("""(?i)refund\s+(?:of\s+)?\$([\d,]+\.\d{2})"""),
)
```

**Step 3: Update parse() to detect direction and counterparty**

Add a new extended parse result:
```kotlin
data class ParsedTransaction(
    val amount: Double,
    val merchant: String,
    val category: String,
    val rawText: String,
    val direction: String = "debit",
    val counterparty: String = "",
)
```

In the `parse()` method, try P2P patterns before falling through to standard parsing. Set `direction = "credit"` for received money and `category = "transfer"`.

**Step 4: Update parseAndStore to set direction/counterparty on entity**

Pass the `direction` and `counterparty` from `ParsedTransaction` to the `TransactionEntity` constructor.

**Step 5: Commit**
```
feat(android): P2P payment tracking — Venmo, PayPal, Cash App, Zelle, income detection
```

---

### Task A4: Income Cycle Detector

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/finance/IncomeCycleDetector.kt`

**Step 1: Create IncomeCycleDetector**

```kotlin
package com.jarvis.assistant.feature.finance

import android.content.Context
import com.jarvis.assistant.data.dao.RecurringPatternDao
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.entity.RecurringPatternEntity
import dagger.hilt.android.qualifiers.ApplicationContext
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import javax.inject.Inject
import javax.inject.Singleton
import kotlin.math.abs

@Singleton
class IncomeCycleDetector @Inject constructor(
    @ApplicationContext private val context: Context,
    private val transactionDao: TransactionDao,
    private val recurringPatternDao: RecurringPatternDao,
) {
    companion object {
        private const val TAG = "IncomeCycleDetector"
        private const val AMOUNT_TOLERANCE = 0.05 // 5%
        private const val MIN_OCCURRENCES = 2
    }

    suspend fun detectCycles() {
        val fmt = DateTimeFormatter.ofPattern("yyyy-MM-dd")
        val since = LocalDate.now().minusDays(90).format(fmt)
        val today = LocalDate.now().format(fmt)

        val credits = transactionDao.getTransactionsInRange(since, today)
            .filter { it.direction == "credit" && it.amount > 100.0 }

        // Group by approximate amount (±5%)
        val groups = mutableListOf<MutableList<Pair<String, Double>>>()
        for (tx in credits) {
            val matched = groups.find { group ->
                group.any { (_, amt) -> abs(tx.amount - amt) / amt < AMOUNT_TOLERANCE }
            }
            if (matched != null) {
                matched.add(tx.date to tx.amount)
            } else {
                groups.add(mutableListOf(tx.date to tx.amount))
            }
        }

        // For groups with 2+ occurrences, detect period
        for (group in groups.filter { it.size >= MIN_OCCURRENCES }) {
            val dates = group.map { (d, _) -> LocalDate.parse(d, fmt) }.sorted()
            val intervals = dates.zipWithNext { a, b -> ChronoUnit.DAYS.between(a, b) }
            val medianInterval = intervals.sorted()[intervals.size / 2]
            val period = classifyPeriod(medianInterval) ?: continue
            val avgAmount = group.map { it.second }.average()

            val existing = recurringPatternDao.findByMerchant(
                merchant = "Income",
                direction = "credit",
            )
            val entity = RecurringPatternEntity(
                id = existing?.id ?: 0,
                merchant = "Income",
                normalizedAmount = avgAmount,
                period = period,
                direction = "credit",
                counterparty = group.firstOrNull()?.let { "" } ?: "",
                lastSeen = dates.last().format(fmt),
                firstSeen = dates.first().format(fmt),
                isActive = true,
                occurrenceCount = group.size,
            )
            recurringPatternDao.upsert(entity)
        }
    }

    private fun classifyPeriod(days: Long): String? = when (days) {
        in 5..9 -> "WEEKLY"
        in 12..16 -> "BIWEEKLY"
        in 27..35 -> "MONTHLY"
        in 85..100 -> "QUARTERLY"
        in 355..375 -> "ANNUAL"
        else -> null
    }
}
```

**Step 2: Wire into SyncWorker**

Add `IncomeCycleDetector` to `SyncWorker` constructor. Call `incomeCycleDetector.detectCycles()` with a 24-hour throttle (alongside existing step 9 relationship alerts).

**Step 3: Commit**
```
feat(android): income cycle detector — auto-learns pay schedule from deposit patterns
```

---

### Task A5: Sleep Quality Estimator

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/health/SleepEstimator.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/health/ScreenStateTracker.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/service/JarvisService.kt`

**Step 1: Create ScreenStateTracker**

Registers a `BroadcastReceiver` for `ACTION_SCREEN_ON`/`ACTION_SCREEN_OFF`. Maintains an in-memory list of screen events with timestamps. Used by `SleepEstimator` to infer sleep onset/wake.

```kotlin
package com.jarvis.assistant.feature.health

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class ScreenStateTracker @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    data class ScreenEvent(val isOn: Boolean, val timestamp: Long)

    private val events = mutableListOf<ScreenEvent>()
    private val maxEvents = 500
    @Volatile private var registered = false

    private val receiver = object : BroadcastReceiver() {
        override fun onReceive(ctx: Context, intent: Intent) {
            val isOn = intent.action == Intent.ACTION_SCREEN_ON
            synchronized(events) {
                events.add(ScreenEvent(isOn, System.currentTimeMillis()))
                if (events.size > maxEvents) events.removeAt(0)
            }
        }
    }

    fun register() {
        if (registered) return
        val filter = IntentFilter().apply {
            addAction(Intent.ACTION_SCREEN_ON)
            addAction(Intent.ACTION_SCREEN_OFF)
        }
        context.registerReceiver(receiver, filter)
        registered = true
    }

    fun unregister() {
        if (!registered) return
        try { context.unregisterReceiver(receiver) } catch (_: Exception) {}
        registered = false
    }

    fun getEventsSince(sinceMs: Long): List<ScreenEvent> = synchronized(events) {
        events.filter { it.timestamp >= sinceMs }.toList()
    }
}
```

**Step 2: Create SleepEstimator**

```kotlin
package com.jarvis.assistant.feature.health

import com.jarvis.assistant.data.dao.SleepSessionDao
import com.jarvis.assistant.data.entity.SleepSessionEntity
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.concurrent.TimeUnit
import javax.inject.Inject
import javax.inject.Singleton

@Singleton
class SleepEstimator @Inject constructor(
    private val screenStateTracker: ScreenStateTracker,
    private val sleepSessionDao: SleepSessionDao,
) {
    companion object {
        private const val MIN_SLEEP_HOURS = 3
        private const val MAX_SLEEP_HOURS = 14
        private const val MIN_GAP_MS = MIN_SLEEP_HOURS * 3_600_000L
        private const val MAX_GAP_MS = MAX_SLEEP_HOURS * 3_600_000L
    }

    suspend fun estimateLastNight(): SleepSessionEntity? {
        val now = System.currentTimeMillis()
        val yesterday6pm = now - TimeUnit.HOURS.toMillis(18)
        val events = screenStateTracker.getEventsSince(yesterday6pm)

        // Find the longest screen-off gap between 8pm-noon as sleep window
        val offEvents = events.filter { !it.isOn }
        val onEvents = events.filter { it.isOn }

        var bestOnset = 0L
        var bestWake = 0L
        var bestDuration = 0L

        for (off in offEvents) {
            val hour = Instant.ofEpochMilli(off.timestamp).atZone(ZoneId.systemDefault()).hour
            if (hour < 20 && hour > 12) continue // Skip daytime offs

            val nextOn = onEvents.filter { it.timestamp > off.timestamp }.minByOrNull { it.timestamp }
            val wake = nextOn?.timestamp ?: continue
            val duration = wake - off.timestamp

            if (duration in MIN_GAP_MS..MAX_GAP_MS && duration > bestDuration) {
                bestOnset = off.timestamp
                bestWake = wake
                bestDuration = duration
            }
        }

        if (bestDuration == 0L) return null

        // Count interruptions (screen-on events during sleep window)
        val interruptions = events.count { it.isOn && it.timestamp in bestOnset..bestWake }

        val durationMin = (bestDuration / 60_000).toInt()
        val quality = computeQuality(durationMin, interruptions)
        val date = Instant.ofEpochMilli(bestWake).atZone(ZoneId.systemDefault())
            .toLocalDate().format(DateTimeFormatter.ofPattern("yyyy-MM-dd"))

        // Skip if already recorded for this date
        val existing = sleepSessionDao.getLatest()
        if (existing?.date == date) return existing

        val session = SleepSessionEntity(
            onsetTime = bestOnset,
            wakeTime = bestWake,
            durationMinutes = durationMin,
            qualityScore = quality,
            interruptions = interruptions,
            date = date,
        )
        sleepSessionDao.insert(session)
        return session
    }

    private fun computeQuality(durationMin: Int, interruptions: Int): Int {
        var score = 80
        // Duration: ideal is 420-480 min (7-8 hours)
        val durationHours = durationMin / 60.0
        if (durationHours < 6) score -= ((6 - durationHours) * 10).toInt()
        if (durationHours > 9) score -= ((durationHours - 9) * 5).toInt()
        // Interruptions
        score -= interruptions * 5
        return score.coerceIn(0, 100)
    }
}
```

**Step 3: Wire into JarvisService**

Add `ScreenStateTracker` to `JarvisService` injected fields. Call `screenStateTracker.register()` in `onCreate()`, `screenStateTracker.unregister()` in `onDestroy()`. Add `SleepEstimator` to `SyncWorker` — call `sleepEstimator.estimateLastNight()` once per day (6-hour throttle, runs in the morning).

**Step 4: Commit**
```
feat(android): sleep quality estimator — passive detection from screen on/off patterns
```

---

### Task A6: Screen Time Intelligence

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/health/ScreenTimeAnalyzer.kt`

**Step 1: Create ScreenTimeAnalyzer**

Uses `UsageStatsManager` to query per-app foreground time. Categorizes apps, computes late-night usage, and generates weekly insights. Called from `SyncWorker` with a 24-hour throttle.

Key implementation details:
- `PACKAGE_CATEGORIES` map: social media, productivity, entertainment, communication package names
- `getUsageForDay(date)`: queries `UsageStatsManager.queryUsageStats(INTERVAL_DAILY, ...)`, groups by category
- `getLateNightUsage(date)`: queries `UsageStatsManager.queryEvents(...)` between 10pm-3am, sums social media duration
- `getWeeklyInsights()`: compares this week's totals to previous week, returns text summary for health briefing

This feature requires `PACKAGE_USAGE_STATS` permission — add a check in `SettingsScreen` that opens `Settings.ACTION_USAGE_ACCESS_SETTINGS` if not granted.

**Step 2: Commit**
```
feat(android): screen time intelligence — per-category usage analysis with late-night tracking
```

---

### Task A7: Interest Learning Engine (Desktop)

**Files:**
- Create: `engine/src/jarvis_engine/news/__init__.py`
- Create: `engine/src/jarvis_engine/news/interests.py`
- Create: `engine/tests/test_news_interests.py`

**Step 1: Write tests for InterestLearner**

```python
class TestInterestLearner:
    def test_record_interest_creates_entry(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("technology", weight=1.0)
        profile = learner.get_profile()
        assert "technology" in profile
        assert profile["technology"] > 0

    def test_decay_reduces_stale_interests(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("sports", weight=1.0)
        learner._decay_all(days=60)  # force 60-day decay
        profile = learner.get_profile()
        assert profile.get("sports", 0) < 0.5

    def test_negative_interest_reduces_score(self, tmp_path):
        learner = InterestLearner(tmp_path)
        learner.record_interest("sports", weight=1.0)
        learner.record_interest("sports", weight=-0.5)
        profile = learner.get_profile()
        assert profile["sports"] < 1.0
```

**Step 2: Run tests to verify they fail**

**Step 3: Implement InterestLearner**

```python
"""Interest learning engine for news personalization."""
import json
import math
import threading
from datetime import datetime, timezone
from pathlib import Path

_LOCK = threading.Lock()
_HALF_LIFE_DAYS = 30.0

class InterestLearner:
    def __init__(self, root: Path) -> None:
        self._path = root / ".planning" / "runtime" / "interests.json"
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _load(self) -> dict:
        if self._path.exists():
            return json.loads(self._path.read_text())
        return {}

    def _save(self, data: dict) -> None:
        self._path.write_text(json.dumps(data, indent=2))

    def record_interest(self, topic: str, *, weight: float = 0.3) -> None:
        topic = topic.lower().strip()
        with _LOCK:
            data = self._load()
            entry = data.get(topic, {"score": 0.0, "count": 0, "last_seen": ""})
            entry["score"] = max(0.0, entry["score"] + weight)
            entry["count"] = entry.get("count", 0) + 1
            entry["last_seen"] = datetime.now(timezone.utc).isoformat()
            data[topic] = entry
            self._save(data)

    def get_profile(self, *, top_n: int = 20) -> dict[str, float]:
        with _LOCK:
            data = self._load()
        now = datetime.now(timezone.utc)
        result = {}
        for topic, entry in data.items():
            last = entry.get("last_seen", "")
            if not last:
                continue
            try:
                dt = datetime.fromisoformat(last)
                days_ago = (now - dt).total_seconds() / 86400
            except (ValueError, TypeError):
                days_ago = 90.0
            decayed = entry["score"] * math.pow(0.5, days_ago / _HALF_LIFE_DAYS)
            if decayed > 0.01:
                result[topic] = round(decayed, 3)
        return dict(sorted(result.items(), key=lambda x: -x[1])[:top_n])

    def _decay_all(self, *, days: int) -> None:
        """Test helper: simulate passage of time."""
        with _LOCK:
            data = self._load()
            past = datetime.now(timezone.utc).isoformat()
            for entry in data.values():
                if entry.get("last_seen"):
                    from datetime import timedelta
                    dt = datetime.fromisoformat(entry["last_seen"])
                    entry["last_seen"] = (dt - timedelta(days=days)).isoformat()
            self._save(data)
```

**Step 4: Run tests to verify pass**

**Step 5: Commit**
```
feat(engine): interest learning engine — exponential decay topic profiling for news
```

---

## Phase B: Intelligence Layer

### Task B1: Spending Velocity Monitor

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/finance/SpendingVelocityMonitor.kt`

**Implementation:** Queries `TransactionDao.getTransactionsInRange()` for current 7-day and rolling 30-day periods. Groups by `normalizedMerchant` category. Alerts when any category's 7-day total exceeds 1.8x the 30-day weekly average. Routes through `NotificationPriority.IMPORTANT`. Butler message format: "Sir, you've spent $X on [category] this week. Your typical weekly spend is $Y."

Wire into `SyncWorker` with 24-hour throttle.

**Commit:** `feat(android): spending velocity monitor — category-level pace alerts`

---

### Task B2: Recurring Charge Discoverer

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/finance/RecurringChargeDiscoverer.kt`

**Implementation:** Weekly run from `SyncWorker`. Queries 90 days of transactions, groups by `normalizedMerchant`, detects consistent intervals (±3 days for monthly). Stores in `RecurringPatternEntity`. Ghost detection: cross-reference merchants against `PackageManager.getInstalledApplications()` names. Subscription creep: compare monthly total vs 3 months ago.

**Commit:** `feat(android): recurring charge discoverer — auto-detect subscriptions, ghost/creep alerts`

---

### Task B3: Promise Tracker (Desktop)

**Files:**
- Create: `engine/src/jarvis_engine/commitments.py`
- Create: `engine/tests/test_commitments.py`
- Modify: `engine/src/jarvis_engine/mobile_api.py` — add `POST /commitment-scan` endpoint
- Modify: `engine/src/jarvis_engine/proactive/triggers.py` — add `check_commitment_deadlines` trigger

**Implementation:**
- `CommitmentExtractor` class: regex patterns for "I'll", "I will", "let me", "I need to", "by [day]", "before [time]". Falls back to LLM extraction via gateway if available.
- `commitment_tracking` JSON file (same pattern as `missions.json`): `{id, source_contact, direction, commitment_text, inferred_deadline, status, created_at}`.
- Deadline inference: parse relative dates ("tomorrow" → +1 day, "Friday" → next Friday, "next week" → +7 days). Default 72 hours.
- New trigger rule: `check_commitment_deadlines()` with 180-minute cooldown. Fires when outbound commitment within 4 hours of deadline, or inbound commitment 24 hours past.
- Mobile API endpoint `POST /commitment-scan`: accepts `{text, contact}`, returns extracted commitments.

**Commit:** `feat(engine): promise tracker — extract and track commitments from conversations`

---

### Task B4: Unanswered Message Detector (Android)

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/communication/UnansweredMessageDetector.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/scheduling/JarvisNotificationListenerService.kt`

**Implementation:**
- On incoming message notification from messaging apps (WhatsApp, Signal, Telegram, SMS), insert `PendingReplyEntity` with contact importance from `ContactContextDao`.
- Resolution: detect outbound SMS via `ContentObserver` on `content://sms/sent`. For other apps, detect "message sent" notifications.
- Periodic check in `SyncWorker` (5-min throttle): scan unresolved entries. Reminder threshold scaled by importance (1.0→1hr, 0.5→4hr, 0.2→8hr). Below 0.2→never.
- ROUTINE notification with "Reply" and "Dismiss" action buttons.

**Commit:** `feat(android): unanswered message detector — importance-weighted reply reminders`

---

### Task B5: Departure Oracle (Android + Desktop)

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/commute/TrafficChecker.kt` — upgrade to calendar-aware departure nudges
- Modify: `engine/src/jarvis_engine/mobile_api.py` — add `GET /travel-time` endpoint

**Implementation:**
- Desktop `GET /travel-time?origin=lat,lon&dest=lat,lon`: uses web search to estimate travel time, or calculates straight-line distance with average speed multiplier. Returns `{minutes, route_summary}`.
- Android `DepartureOracle` (rename/upgrade `TrafficChecker`): queries `CalendarContract.Instances` for events with non-empty `EVENT_LOCATION` starting in next 2 hours. Calls desktop `/travel-time`. Back-calculates departure time. Only nudges if today's estimate exceeds 7-day average by 10+ minutes (avoid constant "time to leave").
- IMPORTANT notification with "Set Alarm" action button.
- Log commute duration to `CommuteLogEntity` when DRIVING context ends.

**Commit:** `feat: departure oracle — calendar-aware traffic-powered departure nudges`

---

### Task B6: Schedule Guardian (Android)

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/scheduling/ConflictDetector.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/scheduling/JarvisNotificationListenerService.kt`

**Implementation:**
- Before `CalendarEventCreator.createEvent()`, query `CalendarContract.Instances` for the proposed time range. If overlap, post IMPORTANT notification with conflict details.
- Suggest alternatives: scan same week for gaps >= event duration + 30min buffer.
- Integrate into the notification listener pipeline alongside `SchedulingCueExtractor`.

**Commit:** `feat(android): schedule guardian — auto-detect calendar conflicts from notifications`

---

## Phase C: Butler Features

### Task C1: Pre-Bill Awareness Alerts

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/finance/PreBillAlertWorker.kt`

**Implementation:** Daily check from `SyncWorker`. For each active `RecurringPatternEntity`, calculate `expected_next = lastSeen + period`. If within 2 days: ROUTINE notification (IMPORTANT if > $50 or price changed). Butler message: "Sir, Netflix ($15.99) will likely charge in 2 days."

**Commit:** `feat(android): pre-bill alerts — 2-day advance warning with price-change context`

---

### Task C2: Proximity Errand Butler

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/errands/ErrandProximityChecker.kt`
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/errands/ErrandCueExtractor.kt`
- Modify: `engine/src/jarvis_engine/mobile_api.py` — add `POST /errand`, `GET /errands`
- Modify: `engine/src/jarvis_engine/knowledge/facts.py` — add errand category to FactExtractor

**Implementation:**
- `ErrandCueExtractor`: detects errand patterns from notifications ("prescription ready", "package delivered", "order ready for pickup"). Inserts `ErrandEntity` with store name and confidence.
- Desktop `/errand` POST endpoint: creates errand from conversation ("I need to grab dog food"). Desktop `FactExtractor` gets new `errand` category for KG extraction.
- `ErrandProximityChecker` in `SyncWorker` (2-min throttle): compares GPS against known store locations from `CommuteLocationEntity` + errand store coordinates. Haversine < 300m triggers check for pending errands. Per-store 6-hour cooldown.
- IMPORTANT notification with "View List" / "Not Now" action buttons.

**Commit:** `feat: proximity errand butler — passive errand harvesting with GPS-triggered reminders`

---

### Task C3: Sedentary Break Nudges

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/health/SedentaryDetector.kt`

**Implementation:** Uses `Sensor.TYPE_STEP_COUNTER` (hardware, battery-efficient) to track steps-per-hour. If zero steps for 90+ minutes during `NORMAL` context, fires ROUTINE nudge. Wired through existing `NudgeResponseTracker` for adaptive suppression. 45-min minimum between nudges. Butler message: "Sir, you've been at your desk for 2 hours. A short walk resets focus."

Wire into `SyncWorker` with 5-min throttle (same cadence as existing `nudgeEngine.checkAndDeliver()`).

**Commit:** `feat(android): sedentary break nudges — context-aware movement reminders`

---

### Task C4: Habit Streak Detection

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/health/HabitStreakDetector.kt`

**Implementation:** Daily analysis of step counter + screen usage patterns. Groups events by 30-min time-of-day buckets over 14-day window. Behavior in same bucket on >= 70% of applicable days = detected habit. Stores streak length in `HabitPatternEntity` (reuse existing table). Streak break nudge only if habit active 7+ days and user has responded positively to habit nudges.

Wire into `SyncWorker` alongside existing `patternDetector.detectPatterns()` (24-hour throttle).

**Commit:** `feat(android): habit streak detection — passive behavior pattern recognition`

---

### Task C5: Enhanced Parking Valet

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/commute/ParkingMemory.kt`

**Implementation:**
- After Bluetooth disconnect, sample barometer (`Sensor.TYPE_PRESSURE`) for 2 minutes. Count floor changes (0.4 hPa ≈ 1 floor). Store `floor` on `ParkingEntity`.
- Meter tracking: add "Add Timer" action button on parking notification (30min/1hr/2hr). Store `meterExpiresAt`. At 10 min before expiry, fire URGENT notification.
- Passive meter detection: if `NotificationListenerService` sees ParkMobile/SpotHero notification, extract time and auto-set `meterExpiresAt`.
- "Navigate" action button: `google.navigation:q=$lat,$lon&mode=w` (walking mode).

**Commit:** `feat(android): enhanced parking valet — floor detection, meter tracking, navigation`

---

### Task C6: Late-Running Courtesy

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/automation/LateDetector.kt`

**Implementation:** Every 2 min in `SyncWorker`, scan calendar events with attendees starting in next 30 min. If user should have departed (based on `/travel-time`) but hasn't, or is DRIVING with ETA past event start: fire IMPORTANT notification. "Send ETA" button opens `Intent.ACTION_SENDTO` with pre-composed message. Attendee phone lookup via `CalendarContract.Attendees` → `ContactsContract`. Once per event, only for delays > 5 min.

**Commit:** `feat(android): late-running courtesy — proactive ETA sharing with calendar attendees`

---

### Task C7: Morning News Digest (Desktop)

**Files:**
- Create: `engine/src/jarvis_engine/news/aggregator.py`
- Create: `engine/src/jarvis_engine/news/dedup.py`
- Create: `engine/tests/test_news_aggregator.py`
- Modify: `engine/src/jarvis_engine/mobile_api.py` — add `GET /news-digest`
- Modify: `engine/src/jarvis_engine/main.py` — add `news-digest` CLI command

**Step 1: Write tests**

```python
class TestNewsDeduplicator:
    def test_identical_headlines_dedup(self):
        dedup = NewsDeduplicator()
        items = [
            {"headline": "Earthquake hits Turkey", "url": "https://a.com/1"},
            {"headline": "Earthquake hits Turkey", "url": "https://b.com/2"},
        ]
        result = dedup.deduplicate(items)
        assert len(result) == 1

    def test_similar_headlines_dedup(self):
        dedup = NewsDeduplicator()
        items = [
            {"headline": "Major earthquake strikes Turkey", "url": "https://a.com/1"},
            {"headline": "Turkey hit by major earthquake", "url": "https://b.com/2"},
        ]
        result = dedup.deduplicate(items)
        assert len(result) == 1

class TestNewsAggregator:
    def test_digest_returns_max_7_items(self, tmp_path):
        agg = NewsAggregator(tmp_path)
        with patch("jarvis_engine.news.aggregator.search_web") as mock_search:
            mock_search.return_value = []
            result = agg.generate_digest()
            assert len(result.get("headlines", [])) <= 7
```

**Step 2: Implement NewsDeduplicator**

URL normalization + Jaro-Winkler headline similarity > 0.85 = same story. Uses `jellyfish.jaro_winkler_similarity` (already a dependency). Picks cluster representative by shortest headline.

**Step 3: Implement NewsAggregator**

Uses existing `search_web()` + `fetch_page_text()`. Queries "top world news today" plus interest-specific queries from `InterestLearner.get_profile()`. LLM summarization via gateway (Kimi K2): one-sentence summaries, ranked by importance, top 7. Source diversity: max 2 per domain. Clickbait filter: skip headlines matching known patterns.

**Step 4: Add `GET /news-digest` endpoint**

Returns `{"ok": true, "headlines": [...], "local": [...], "generated_utc": "..."}`. Add entry to `_GET_DISPATCH`.

**Step 5: Integrate into MorningBriefing**

In `MorningBriefing.kt`, after getting the ops briefing, also call `GET /news-digest` and append headlines to the notification text.

**Step 6: Commit**
```
feat(engine): morning news digest — personalized headlines with dedup and interest matching
```

---

### Task C8: Breaking News Alerts (Desktop)

**Files:**
- Create: `engine/src/jarvis_engine/news/breaking.py`
- Create: `engine/tests/test_breaking_news.py`
- Modify: `engine/src/jarvis_engine/main.py` — add to daemon loop

**Step 1: Write tests**

```python
class TestBreakingNewsMonitor:
    def test_story_needs_3_sources(self, tmp_path):
        monitor = BreakingNewsMonitor(tmp_path)
        # Story from only 2 sources should not fire
        stories = [
            {"headline": "War breaks out", "domain": "reuters.com"},
            {"headline": "War breaks out", "domain": "bbc.com"},
        ]
        assert not monitor._meets_corroboration_threshold(stories)

    def test_story_with_3_sources_fires(self, tmp_path):
        monitor = BreakingNewsMonitor(tmp_path)
        stories = [
            {"headline": "War breaks out", "domain": "reuters.com"},
            {"headline": "War breaks out", "domain": "bbc.com"},
            {"headline": "War breaks out", "domain": "cnn.com"},
        ]
        assert monitor._meets_corroboration_threshold(stories)

    def test_rate_limit_blocks_frequent_alerts(self, tmp_path):
        monitor = BreakingNewsMonitor(tmp_path)
        monitor._record_alert("story1")
        monitor._record_alert("story2")
        monitor._record_alert("story3")
        assert not monitor._under_rate_limit()
```

**Step 2: Implement BreakingNewsMonitor**

- `check()` method: searches "breaking news world" via `search_web()`, fetches top results, extracts headlines, groups by semantic similarity.
- `_meets_corroboration_threshold()`: requires 3+ distinct domains.
- LLM severity scoring via gateway: strict 1-10 scale prompt. Only fires for score >= 7.
- Rate limiting: max 1 per 6 hours, 3 per 24 hours. Uses `breaking_news_alerts.jsonl` for history.
- Enqueues via `alert_queue.enqueue_alert()` with `priority: "urgent"`.

**Step 3: Wire into daemon loop**

Add to `_cmd_daemon_run_impl()` with a 50-cycle interval (every ~25 min at 30s cycles). Uses lazy import and try/except for graceful degradation.

**Step 4: Commit**
```
feat(engine): breaking news alerts — 3-source corroboration, LLM severity scoring, rate-limited
```

---

### Task C9: Weekly Health Briefing

**Files:**
- Modify: `engine/src/jarvis_engine/mobile_api.py` — add `GET /health-summary`
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/automation/MorningBriefing.kt` — add weekly health section

**Implementation:**
- Desktop `GET /health-summary`: queries KG for sleep, step, screen time facts synced from phone. Generates LLM narrative summary via gateway.
- Android: in `MorningBriefing.onWakeUp()`, check if it's Monday. If so, also call `GET /health-summary` and append to briefing.
- If desktop unavailable, phone generates a local summary from `SleepSessionDao.getInRange()` for the past 7 days.

**Commit:** `feat: weekly health briefing — Monday morning synthesis of sleep, movement, screen time`

---

## Phase D: Polish & Integration

### Task D1: Global Notification Budget

**Files:**
- Create: `android/app/src/main/java/com/jarvis/assistant/feature/notifications/NotificationBudget.kt`
- Modify: All notification-posting code in Phase A-C features

**Implementation:**
- `NotificationBudget` singleton: tracks daily notification count per feature type in SharedPreferences. Hard cap: 8 proactive notifications/day (resets at midnight). Breaking news exempt.
- `canPost(featureType: String): Boolean` — checks count < 8 and per-feature cooldown.
- `recordPost(featureType: String)` — increments count.
- Integrate: all new features call `notificationBudget.canPost()` before posting. Existing features (`RelationshipAutopilot`, `MeetingPrepService`, etc.) are also wired in.

**Commit:** `feat(android): global notification budget — max 8 proactive alerts/day`

---

### Task D2: Settings Screen Toggles

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsScreen.kt`
- Modify: `android/app/src/main/java/com/jarvis/assistant/ui/settings/SettingsViewModel.kt`

**Implementation:** Add new sections to the settings LazyColumn:

- **Financial Intelligence:** toggle spending velocity alerts, pre-bill alerts, subscription tracking
- **Health & Habits:** toggle sedentary nudges, sleep tracking, habit detection; button to grant PACKAGE_USAGE_STATS permission
- **Schedule & Location:** toggle departure oracle, errand proximity, parking meter alerts, late-running courtesy
- **Communication:** toggle unanswered message reminders, promise tracking
- **News:** toggle morning digest, breaking news alerts; slider for breaking news severity threshold (7-10)
- **Notification Budget:** slider for daily cap (4-15, default 8)

Each toggle reads/writes SharedPreferences via ViewModel StateFlow, following existing `callScreenEnabled` pattern.

**Commit:** `feat(android): settings screen — toggles for all 20 life automation features`

---

### Task D3: NotificationChannelManager Updates

**Files:**
- Modify: `android/app/src/main/java/com/jarvis/assistant/feature/notifications/NotificationChannelManager.kt`

**Implementation:** Update `classifyPriority()` mapping to include all new alert types:

```kotlin
// URGENT additions:
"breaking_news", "parking_meter_expiring"

// IMPORTANT additions:
"spending_velocity", "commitment_reminder", "departure_alert",
"schedule_conflict", "late_running", "errand_proximity",
"subscription_creep", "ghost_subscription"

// ROUTINE additions:
"pre_bill_alert", "unanswered_message", "sedentary_nudge",
"habit_streak", "income_arrived", "sleep_summary",
"weekly_health", "weekly_communication", "news_digest"
```

**Commit:** `feat(android): classify all 20 feature notification types into priority channels`

---

### Task D4: Desktop Engine Tests

**Files:**
- Create: `engine/tests/test_commitments.py`
- Create: `engine/tests/test_news_aggregator.py`
- Create: `engine/tests/test_breaking_news.py`
- Modify: `engine/tests/test_proactive.py` — add tests for new trigger rules
- Modify: `engine/tests/test_mobile_api.py` — add tests for new endpoints

**Implementation:** Follow existing test patterns:
- Proactive trigger tests: call check functions directly with snapshot dicts, assert on returned alert strings
- Mobile API tests: use `mobile_server` fixture with `signed_headers()` and `http_request()`
- Unit tests: class-based grouping, no fixtures needed for pure logic

Target: 100+ new tests across all new desktop modules.

**Commit:** `test(engine): comprehensive tests for commitments, news, breaking alerts, new triggers`

---

### Task D5: Final Integration & Anti-Spam Verification

**Files:**
- All files modified in phases A-C

**Implementation:**
- Verify all features route through `NudgeResponseTracker` for adaptive suppression
- Verify all features check `NotificationBudget.canPost()` before posting
- Verify all features respect context filter (only URGENT during MEETING/DRIVING/SLEEPING)
- Verify `NotificationLearner` integration for long-term priority adjustment
- Run full test suite: `python -m pytest engine/tests/ -x -q` — all tests pass
- Manual smoke test: daemon run + mobile API + widget

**Commit:** `chore: verify anti-spam pipeline across all 20 life automation features`

---

## Execution Summary

| Phase | Tasks | Features | Estimated Scope |
|-------|-------|----------|-----------------|
| A: Foundation | 7 tasks | Room migration, merchant normalizer, P2P tracker, income detector, sleep estimator, screen time, interest learner | Data layer + passive detection |
| B: Intelligence | 6 tasks | Spending velocity, recurring charges, promise tracker, unanswered messages, departure oracle, schedule guardian | Analysis + alerting logic |
| C: Butler | 9 tasks | Pre-bill alerts, errand butler, sedentary nudges, habit streaks, parking valet, late courtesy, news digest, breaking news, health briefing | User-facing features |
| D: Polish | 5 tasks | Notification budget, settings, channel mapping, tests, integration | Quality + configuration |

**Total: 27 tasks across 4 phases.**
