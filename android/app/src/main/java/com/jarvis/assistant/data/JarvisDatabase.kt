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
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.CommuteLocationEntity
import com.jarvis.assistant.data.entity.ContextStateEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import com.jarvis.assistant.data.entity.ExtractedEventEntity
import com.jarvis.assistant.data.entity.MedicationEntity
import com.jarvis.assistant.data.entity.MedicationLogEntity
import com.jarvis.assistant.data.entity.NotificationLogEntity
import com.jarvis.assistant.data.entity.ParkingEntity
import com.jarvis.assistant.data.entity.SpamEntity
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
    ],
    version = 7,
    exportSchema = false,
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

    companion object {

        /** v1 -> v2: Add spam_numbers table. */
        val MIGRATION_1_2 = object : Migration(1, 2) {
            override fun migrate(db: SupportSQLiteDatabase) {
                db.execSQL(
                    """
                    CREATE TABLE IF NOT EXISTS  (
                         TEXT NOT NULL,
                         REAL NOT NULL,
                         INTEGER NOT NULL,
                         REAL NOT NULL,
                         REAL NOT NULL,
                         TEXT NOT NULL,
                         INTEGER NOT NULL,
                         TEXT NOT NULL DEFAULT 'auto',
                        PRIMARY KEY()
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
                    CREATE TABLE IF NOT EXISTS  (
                         TEXT NOT NULL,
                         TEXT NOT NULL,
                         INTEGER NOT NULL,
                         INTEGER NOT NULL,
                         TEXT NOT NULL,
                         TEXT NOT NULL,
                         INTEGER NOT NULL DEFAULT 0,
                         INTEGER NOT NULL DEFAULT 0,
                         INTEGER NOT NULL DEFAULT 0,
                         INTEGER NOT NULL,
                        PRIMARY KEY()
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
                    CREATE TABLE IF NOT EXISTS  (
                        uid=197612(Conner) gid=197121 groups=197121 INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                         INTEGER NOT NULL,
                         TEXT NOT NULL,
                         TEXT NOT NULL,
                         TEXT NOT NULL,
                         TEXT NOT NULL,
                         INTEGER NOT NULL,
                         INTEGER NOT NULL
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
                    CREATE TABLE IF NOT EXISTS  (
                        uid=197612(Conner) gid=197121 groups=197121 INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                         TEXT NOT NULL,
                         REAL NOT NULL,
                         TEXT NOT NULL,
                         INTEGER NOT NULL
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
                )
                .build()
        }
    }
}
