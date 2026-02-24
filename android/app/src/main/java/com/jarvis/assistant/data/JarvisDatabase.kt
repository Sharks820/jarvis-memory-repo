package com.jarvis.assistant.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.MedicationLogDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.ContextStateEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import com.jarvis.assistant.data.entity.ExtractedEventEntity
import com.jarvis.assistant.data.entity.MedicationEntity
import com.jarvis.assistant.data.entity.MedicationLogEntity
import com.jarvis.assistant.data.entity.NotificationLogEntity
import com.jarvis.assistant.data.entity.SpamEntity
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
    ],
    version = 6,
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
                )
                .build()
        }
    }
}
