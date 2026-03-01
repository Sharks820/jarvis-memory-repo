package com.jarvis.assistant.di

import android.content.Context
import com.jarvis.assistant.data.JarvisDatabase
import com.jarvis.assistant.data.dao.CallLogDao
import com.jarvis.assistant.data.dao.CommandQueueDao
import com.jarvis.assistant.data.dao.ContactContextDao
import com.jarvis.assistant.data.dao.ContextStateDao
import com.jarvis.assistant.data.dao.ConversationDao
import com.jarvis.assistant.data.dao.DocumentDao
import com.jarvis.assistant.data.dao.ExtractedEventDao
import com.jarvis.assistant.data.dao.HabitDao
import com.jarvis.assistant.data.dao.NudgeLogDao
import com.jarvis.assistant.data.dao.MedicationDao
import com.jarvis.assistant.data.dao.MedicationLogDao
import com.jarvis.assistant.data.dao.NotificationLogDao
import com.jarvis.assistant.data.dao.SpamDao
import com.jarvis.assistant.data.dao.TransactionDao
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.security.CryptoHelper
import dagger.Module
import dagger.Provides
import dagger.hilt.InstallIn
import dagger.hilt.android.qualifiers.ApplicationContext
import dagger.hilt.components.SingletonComponent
import javax.inject.Singleton

@Module
@InstallIn(SingletonComponent::class)
object AppModule {

    @Provides
    @Singleton
    fun provideCryptoHelper(@ApplicationContext context: Context): CryptoHelper =
        CryptoHelper(context)

    @Provides
    @Singleton
    fun provideDatabase(@ApplicationContext context: Context, crypto: CryptoHelper): JarvisDatabase {
        // Always use the stable fallback passphrase for DB encryption.
        // The signing key is only for HMAC signatures — using it here would change
        // the DB encryption key after bootstrap, making the pre-bootstrap DB unreadable.
        val seed = crypto.getOrCreateFallbackPassphrase()
        return JarvisDatabase.create(context, seed.toByteArray(Charsets.UTF_8))
    }

    @Provides
    fun provideConversationDao(db: JarvisDatabase): ConversationDao =
        db.conversationDao()

    @Provides
    fun provideCommandQueueDao(db: JarvisDatabase): CommandQueueDao =
        db.commandQueueDao()

    @Provides
    fun provideSpamDao(db: JarvisDatabase): SpamDao =
        db.spamDao()

    @Provides
    fun provideExtractedEventDao(db: JarvisDatabase): ExtractedEventDao =
        db.extractedEventDao()

    @Provides
    fun provideNotificationLogDao(db: JarvisDatabase): NotificationLogDao =
        db.notificationLogDao()

    @Provides
    fun provideContextStateDao(db: JarvisDatabase): ContextStateDao =
        db.contextStateDao()

    @Provides
    fun provideMedicationDao(db: JarvisDatabase): MedicationDao =
        db.medicationDao()

    @Provides
    fun provideMedicationLogDao(db: JarvisDatabase): MedicationLogDao =
        db.medicationLogDao()

    @Provides
    fun provideTransactionDao(db: JarvisDatabase): TransactionDao =
        db.transactionDao()

    @Provides
    fun provideCommuteDao(db: JarvisDatabase): CommuteDao =
        db.commuteDao()

    @Provides
    fun provideDocumentDao(db: JarvisDatabase): DocumentDao =
        db.documentDao()

    @Provides
    fun provideContactContextDao(db: JarvisDatabase): ContactContextDao =
        db.contactContextDao()

    @Provides
    fun provideCallLogDao(db: JarvisDatabase): CallLogDao =
        db.callLogDao()

    @Provides
    fun provideHabitDao(db: JarvisDatabase): HabitDao =
        db.habitDao()

    @Provides
    fun provideNudgeLogDao(db: JarvisDatabase): NudgeLogDao =
        db.nudgeLogDao()
}
