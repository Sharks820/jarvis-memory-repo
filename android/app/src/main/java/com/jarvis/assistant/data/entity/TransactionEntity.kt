package com.jarvis.assistant.data.entity

import androidx.room.Entity
import androidx.room.Index
import androidx.room.PrimaryKey

/**
 * Room entity for parsed bank transactions.
 *
 * Each transaction is created from a bank SMS or email notification parsed by
 * [BankNotificationParser]. The [notificationHash] field (SHA-256 of raw text)
 * provides deduplication so the same notification is never stored twice.
 *
 * [category] is one of: "purchase", "subscription", "transfer", "atm", "refund", "fee", "other".
 */
@Entity(
    tableName = "transactions",
    indices = [Index(value = ["notificationHash"], unique = true)],
)
data class TransactionEntity(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    val amount: Double,
    val merchant: String,
    val category: String,
    val sourceApp: String,
    val rawText: String,
    val isAnomaly: Boolean = false,
    val anomalyReason: String = "",
    val date: String,
    val timestamp: Long = System.currentTimeMillis(),
    val notificationHash: String,
    val direction: String = "debit",
    val normalizedMerchant: String = "",
    val counterparty: String = "",
)
