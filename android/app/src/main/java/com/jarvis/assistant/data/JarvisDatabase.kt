package com.jarvis.assistant.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import com.jarvis.assistant.data.entity.ExtractedEventEntity
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
    ],
    version = 4,
    exportSchema = false,
)
abstract class JarvisDatabase : RoomDatabase() {

    abstract fun conversationDao(): ConversationDao
    abstract fun commandQueueDao(): CommandQueueDao
    abstract fun spamDao(): SpamDao
    abstract fun extractedEventDao(): ExtractedEventDao
    abstract fun notificationLogDao(): NotificationLogDao

    companion object {
        fun create(context: Context, passphrase: ByteArray): JarvisDatabase {
            val factory = SupportFactory(passphrase)
            return Room.databaseBuilder(
                context.applicationContext,
                JarvisDatabase::class.java,
                "jarvis.db",
            )
                .openHelperFactory(factory)
                .fallbackToDestructiveMigration()
                .build()
        }
    }
}
