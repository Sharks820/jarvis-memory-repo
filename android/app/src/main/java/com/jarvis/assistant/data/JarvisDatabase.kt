package com.jarvis.assistant.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.MedicationLogDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.data.dao.CallLogDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.NudgeLogDao
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.entity.CallLogEntity
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.ContactContextEntity
import com.jarvis.assistant.data.entity.CommuteLocationEntity
import com.jarvis.assistant.data.entity.ContextStateEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import com.jarvis.assistant.data.entity.ExtractedEventEntity
import com.jarvis.assistant.data.entity.MedicationEntity
import com.jarvis.assistant.data.entity.MedicationLogEntity
import com.jarvis.assistant.data.entity.NotificationLogEntity
import com.jarvis.assistant.data.entity.ParkingEntity
import com.jarvis.assistant.data.entity.ScannedDocumentEntity
import com.jarvis.assistant.data.entity.SpamEntity
import com.jarvis.assistant.data.entity.HabitPatternEntity
import com.jarvis.assistant.data.entity.NudgeLogEntity
import com.jarvis.assistant.data.entity.TransactionEntity
import net.sqlcipher.database.SupportFactory

@Database(
    entities = [
        ConversationEntity::class,
        CommandQueueEntity::class,
        SpamEntity::class,
        ExtractedEventEntity::class,
        NotificationLogEntity::class,
        ContextStateEntity::class,
        MedicationEntity::class,
        MedicationLogEntity::class,
        TransactionEntity::class,
        CommuteLocationEntity::class,
        ParkingEntity::class,
        ScannedDocumentEntity::class,
        ContactContextEntity::class,
        CallLogEntity::class,
        HabitPatternEntity::class,
        NudgeLogEntity::class,
    ],
    version = 11,
    exportSchema = true,
)
abstract class JarvisDatabase : RoomDatabase() {

    abstract fun conversationDao(): ConversationDao
    abstract fun commandQueueDao(): CommandQueueDao
    abstract fun spamDao(): SpamDao
    abstract fun extractedEventDao(): ExtractedEventDao
    abstract fun notificationLogDao(): NotificationLogDao
    abstract fun contextStateDao(): ContextStateDao
    abstract fun medicationDao(): MedicationDao
    abstract fun medicationLogDao(): MedicationLogDao
    abstract fun transactionDao(): TransactionDao
    abstract fun commuteDao(): CommuteDao
    abstract fun documentDao(): DocumentDao
    abstract fun contactContextDao(): ContactContextDao
    abstract fun callLogDao(): CallLogDao
    abstract fun habitDao(): HabitDao
    abstract fun nudgeLogDao(): NudgeLogDao

    companion object {

        /** v1 -> v2: Add spam_numbers table. */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `spam_numbers` (
                        `number` TEXT NOT NULL,
                        `score` REAL NOT NULL,
                        `calls` INTEGER NOT NULL,
                        `missed_ratio` REAL NOT NULL,
                        `avg_duration_s` REAL NOT NULL,
                        `reasons` TEXT NOT NULL,
                        `last_synced` INTEGER NOT NULL,
                        `user_action` TEXT NOT NULL DEFAULT 'auto',
                        PRIMARY KEY(`number`)
                    )
                    """.trimIndent()
                )
            }
        }

        /** v2 -> v3: Add extracted_events table. */
        val MIGRATION_2_3 = object : Migration(2, 3) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `extracted_events` (
                        `content_hash` TEXT NOT NULL,
                        `title` TEXT NOT NULL,
                        `date_time_ms` INTEGER NOT NULL,
                        `end_date_time_ms` INTEGER NOT NULL,
                        `location` TEXT NOT NULL,
                        `source_package` TEXT NOT NULL,
                        `calendar_event_id` INTEGER NOT NULL DEFAULT 0,
                        `desktop_notified` INTEGER NOT NULL DEFAULT 0,
                        `conflict_detected` INTEGER NOT NULL DEFAULT 0,
                        `created_at` INTEGER NOT NULL,
                        PRIMARY KEY(`content_hash`)
                    )
                    """.trimIndent()
                )
            }
        }

        /** v3 -> v4: Add notification_log table. */
        val MIGRATION_3_4 = object : Migration(3, 4) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `notification_log` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `notificationId` INTEGER NOT NULL,
                        `alertType` TEXT NOT NULL,
                        `title` TEXT NOT NULL,
                        `channelId` TEXT NOT NULL,
                        `action` TEXT NOT NULL,
                        `actionDelayMs` INTEGER NOT NULL,
                        `createdAt` INTEGER NOT NULL
                    )
                    """.trimIndent()
                )
            }
        }

        /** v4 -> v5: Add context_state_log table. */
        val MIGRATION_4_5 = object : Migration(4, 5) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `context_state_log` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `context` TEXT NOT NULL,
                        `confidence` REAL NOT NULL,
                        `source` TEXT NOT NULL,
                        `createdAt` INTEGER NOT NULL
                    )
                    """.trimIndent()
                )
            }
        }

        /** v5 -> v6: Add medications and medication_log tables. */
        val MIGRATION_5_6 = object : Migration(5, 6) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `medications` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `name` TEXT NOT NULL,
                        `dosage` TEXT NOT NULL,
                        `frequency` TEXT NOT NULL,
                        `scheduledTimes` TEXT NOT NULL,
                        `pillsRemaining` INTEGER NOT NULL,
                        `pillsPerRefill` INTEGER NOT NULL DEFAULT 30,
                        `refillReminderDays` INTEGER NOT NULL DEFAULT 7,
                        `isActive` INTEGER NOT NULL DEFAULT 1,
                        `notes` TEXT NOT NULL DEFAULT '',
                        `createdAt` INTEGER NOT NULL,
                        `updatedAt` INTEGER NOT NULL
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `medication_log` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `medicationId` INTEGER NOT NULL,
                        `medicationName` TEXT NOT NULL,
                        `scheduledTime` TEXT NOT NULL,
                        `takenAt` INTEGER NOT NULL DEFAULT 0,
                        `status` TEXT NOT NULL,
                        `date` TEXT NOT NULL
                    )
                    """.trimIndent()
                )
            }
        }

        /** v6 -> v7: Add transactions, commute_locations, and parking_locations tables. */
        val MIGRATION_6_7 = object : Migration(6, 7) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `transactions` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `amount` REAL NOT NULL,
                        `merchant` TEXT NOT NULL,
                        `category` TEXT NOT NULL,
                        `sourceApp` TEXT NOT NULL,
                        `rawText` TEXT NOT NULL,
                        `isAnomaly` INTEGER NOT NULL DEFAULT 0,
                        `anomalyReason` TEXT NOT NULL DEFAULT '',
                        `date` TEXT NOT NULL,
                        `timestamp` INTEGER NOT NULL,
                        `notificationHash` TEXT NOT NULL
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS `index_transactions_notificationHash` " +
                        "ON `transactions` (`notificationHash`)",
                )
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `commute_locations` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `label` TEXT NOT NULL,
                        `latitude` REAL NOT NULL,
                        `longitude` REAL NOT NULL,
                        `radius` REAL NOT NULL DEFAULT 200.0,
                        `visitCount` INTEGER NOT NULL DEFAULT 1,
                        `avgArrivalHour` REAL NOT NULL DEFAULT 0.0,
                        `avgDepartureHour` REAL NOT NULL DEFAULT 0.0,
                        `lastVisited` INTEGER NOT NULL,
                        `confidence` REAL NOT NULL DEFAULT 0.05
                    )
                    """.trimIndent()
                )
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `parking_locations` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `latitude` REAL NOT NULL,
                        `longitude` REAL NOT NULL,
                        `accuracy` REAL NOT NULL,
                        `bluetoothDeviceName` TEXT NOT NULL,
                        `timestamp` INTEGER NOT NULL,
                        `isActive` INTEGER NOT NULL DEFAULT 1,
                        `note` TEXT NOT NULL DEFAULT ''
                    )
                    """.trimIndent()
                )
            }
        }

        /** v8 -> v9: Add contact_context and call_interaction_log tables for relationship memory. */
        val MIGRATION_8_9 = object : Migration(8, 9) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `contact_context` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `phoneNumber` TEXT NOT NULL,
                        `contactName` TEXT NOT NULL,
                        `lastCallDate` TEXT NOT NULL DEFAULT '',
                        `lastCallTimestamp` INTEGER NOT NULL DEFAULT 0,
                        `keyTopics` TEXT NOT NULL DEFAULT '[]',
                        `lastNotes` TEXT NOT NULL DEFAULT '',
                        `birthday` TEXT NOT NULL DEFAULT '',
                        `anniversary` TEXT NOT NULL DEFAULT '',
                        `relationship` TEXT NOT NULL DEFAULT 'other',
                        `totalCalls` INTEGER NOT NULL DEFAULT 0,
                        `importance` REAL NOT NULL DEFAULT 0.0,
                        `syncedToDesktop` INTEGER NOT NULL DEFAULT 0,
                        `createdAt` INTEGER NOT NULL,
                        `updatedAt` INTEGER NOT NULL
                    )
                    """.trimIndent(),
                )
                db.execSQL(
                    "CREATE UNIQUE INDEX IF NOT EXISTS `index_contact_context_phoneNumber` " +
                        "ON `contact_context` (`phoneNumber`)",
                )
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `call_interaction_log` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `contactContextId` INTEGER NOT NULL,
                        `phoneNumber` TEXT NOT NULL,
                        `contactName` TEXT NOT NULL,
                        `direction` TEXT NOT NULL,
                        `durationSeconds` INTEGER NOT NULL DEFAULT 0,
                        `notes` TEXT NOT NULL DEFAULT '',
                        `topics` TEXT NOT NULL DEFAULT '[]',
                        `timestamp` INTEGER NOT NULL,
                        `date` TEXT NOT NULL
                    )
                    """.trimIndent(),
                )
            }
        }

        /** v9 -> v10: Add habit_patterns and nudge_log tables for habit engine. */
        val MIGRATION_9_10 = object : Migration(9, 10) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `habit_patterns` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `patternType` TEXT NOT NULL,
                        `label` TEXT NOT NULL,
                        `description` TEXT NOT NULL,
                        `triggerDays` TEXT NOT NULL,
                        `triggerHour` INTEGER NOT NULL,
                        `triggerMinute` INTEGER NOT NULL,
                        `locationLabel` TEXT NOT NULL DEFAULT '',
                        `confidence` REAL NOT NULL DEFAULT 0.0,
                        `occurrenceCount` INTEGER NOT NULL DEFAULT 0,
                        `isActive` INTEGER NOT NULL DEFAULT 1,
                        `isSuppressed` INTEGER NOT NULL DEFAULT 0,
                        `category` TEXT NOT NULL DEFAULT 'custom',
                        `createdAt` INTEGER NOT NULL,
                        `updatedAt` INTEGER NOT NULL
                    )
                    """.trimIndent(),
                )
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `nudge_log` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `patternId` INTEGER NOT NULL,
                        `patternLabel` TEXT NOT NULL,
                        `nudgeText` TEXT NOT NULL,
                        `deliveredAt` INTEGER NOT NULL,
                        `respondedAt` INTEGER NOT NULL DEFAULT 0,
                        `response` TEXT NOT NULL DEFAULT '',
                        `date` TEXT NOT NULL
                    )
                    """.trimIndent(),
                )
            }
        }

        /** v10 -> v11: Add missing indices on foreign key columns for query performance. */
        val MIGRATION_10_11 = object : Migration(10, 11) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_call_interaction_log_contactContextId` " +
                        "ON `call_interaction_log` (`contactContextId`)",
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_medication_log_medicationId` " +
                        "ON `medication_log` (`medicationId`)",
                )
                db.execSQL(
                    "CREATE INDEX IF NOT EXISTS `index_nudge_log_patternId` " +
                        "ON `nudge_log` (`patternId`)",
                )
            }
        }

        /** v7 -> v8: Add scanned_documents table. */
        val MIGRATION_7_8 = object : Migration(7, 8) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS `scanned_documents` (
                        `id` INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        `title` TEXT NOT NULL,
                        `ocrText` TEXT NOT NULL,
                        `category` TEXT NOT NULL,
                        `imagePath` TEXT NOT NULL,
                        `thumbnailPath` TEXT NOT NULL,
                        `fileSize` INTEGER NOT NULL,
                        `ocrConfidence` REAL NOT NULL,
                        `syncedToDesktop` INTEGER NOT NULL DEFAULT 0,
                        `contentHash` TEXT NOT NULL,
                        `createdAt` INTEGER NOT NULL,
                        `updatedAt` INTEGER NOT NULL
                    )
                    """.trimIndent()
                )
            }
        }

        fun create(context: Context, passphrase: ByteArray): JarvisDatabase {
            val factory = SupportFactory(passphrase)
            return Room.databaseBuilder(
                context.applicationContext,
                JarvisDatabase::class.java,
                "jarvis.db",
            )
                .openHelperFactory(factory)
                .addMigrations(
                    MIGRATION_1_2,
                    MIGRATION_2_3,
                    MIGRATION_3_4,
                    MIGRATION_4_5,
                    MIGRATION_5_6,
                    MIGRATION_6_7,
                    MIGRATION_7_8,
                    MIGRATION_8_9,
                    MIGRATION_9_10,
                    MIGRATION_10_11,
                )
                .build()
        }
    }
}
