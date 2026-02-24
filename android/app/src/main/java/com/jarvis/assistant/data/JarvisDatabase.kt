package com.jarvis.assistant.data

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.entity.CommandQueueEntity
import com.jarvis.assistant.data.entity.ConversationEntity
import net.sqlcipher.database.SupportFactory

@Database(
    entities = [ConversationEntity::class, CommandQueueEntity::class],
    version = 1,
    exportSchema = false,
)
abstract class JarvisDatabase : RoomDatabase() {

    abstract fun conversationDao(): ConversationDao
    abstract fun commandQueueDao(): CommandQueueDao

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
