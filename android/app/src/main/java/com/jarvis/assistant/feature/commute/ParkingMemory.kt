package com.jarvis.assistant.feature.commute

import android.Manifest
import android.app.NotificationManager
import android.bluetooth.BluetoothDevice
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.pm.PackageManager
import android.location.LocationManager
import android.os.Build
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import com.jarvis.assistant.R
import com.jarvis.assistant.data.dao.CommuteDao
import com.jarvis.assistant.data.entity.ParkingEntity
import com.jarvis.assistant.feature.notifications.NotificationPriority
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import javax.inject.Inject
import javax.inject.Singleton

/**
 * Saves parking GPS coordinates when a configured car Bluetooth disconnects.
 *
 * The user configures car Bluetooth device names in Settings (comma-separated
 * in SharedPreferences key [PREF_CAR_BT_NAMES]). When any of those devices
 * disconnect, the current GPS position is saved as the active parking location.
 *
 * The BroadcastReceiver is runtime-registered from [JarvisService] (not
 * manifest-registered) so it is only active while the foreground service runs.
 */
@Singleton
class ParkingMemory @Inject constructor(
    @ApplicationContext private val context: Context,
    private val commuteDao: CommuteDao,
) {

    private val notificationManager by lazy {
        context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    }

    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private var receiver: BroadcastReceiver? = null

    /**
     * Register a BroadcastReceiver for Bluetooth device disconnections.
     * Call from [JarvisService.onCreate].
     */
    fun registerBluetoothReceiver() {
        if (receiver != null) return // already registered

        receiver = object : BroadcastReceiver() {
            override fun onReceive(ctx: Context, intent: Intent) {
                if (intent.action != BluetoothDevice.ACTION_ACL_DISCONNECTED) return

                val device = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                    intent.getParcelableExtra(
                        BluetoothDevice.EXTRA_DEVICE,
                        BluetoothDevice::class.java,
                    )
                } else {
                    @Suppress("DEPRECATION")
                    intent.getParcelableExtra(BluetoothDevice.EXTRA_DEVICE)
                }

                val deviceName = try {
                    if (ContextCompat.checkSelfPermission(
                            context,
                            Manifest.permission.BLUETOOTH_CONNECT,
                        ) == PackageManager.PERMISSION_GRANTED
                    ) {
                        device?.name
                    } else {
                        null
                    }
                } catch (e: SecurityException) {
                    Log.d(TAG, "BT name check failed: ${e.message}")
                    null
                }

                if (deviceName == null) {
                    Log.d(TAG, "BT disconnect without name, skipping")
                    return
                }

                val carNames = getConfiguredCarNames()
                if (carNames.isEmpty()) return

                val isCarBt = carNames.any { configured ->
                    deviceName.equals(configured, ignoreCase = true)
                }
                if (!isCarBt) return

                Log.i(TAG, "Car Bluetooth disconnected: $deviceName")
                scope.launch { saveParking(deviceName) }
            }
        }

        val filter = IntentFilter(BluetoothDevice.ACTION_ACL_DISCONNECTED)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            context.registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED)
        } else {
            context.registerReceiver(receiver, filter)
        }
        Log.i(TAG, "Bluetooth disconnect receiver registered")
    }

    /**
     * Unregister the BroadcastReceiver. Call from [JarvisService.onDestroy].
     */
    fun unregisterBluetoothReceiver() {
        scope.coroutineContext[kotlinx.coroutines.Job]?.cancelChildren()
        receiver?.let {
            try {
                context.unregisterReceiver(it)
            } catch (e: IllegalArgumentException) {
                Log.d(TAG, "Receiver already unregistered")
            }
            receiver = null
        }
    }

    /** Get the current active parking location, or null. */
    suspend fun getActiveParking(): ParkingEntity? = commuteDao.getActiveParking()

    // ── Private Helpers ───────────────────────────────────────────────

    @Suppress("MissingPermission")
    private suspend fun saveParking(deviceName: String) {
        if (!hasLocationPermission()) {
            Log.w(TAG, "Location permission not granted, cannot save parking")
            return
        }

        val locationManager =
            context.getSystemService(Context.LOCATION_SERVICE) as LocationManager

        val location = try {
            locationManager.getLastKnownLocation(LocationManager.FUSED_PROVIDER)
                ?: locationManager.getLastKnownLocation(LocationManager.GPS_PROVIDER)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to get location for parking: ${e.message}")
            null
        }

        if (location == null) {
            Log.w(TAG, "No location available for parking save")
            return
        }

        // Atomically deactivate previous parking and save new
        commuteDao.replaceActiveParking(
            ParkingEntity(
                latitude = location.latitude,
                longitude = location.longitude,
                accuracy = location.accuracy,
                bluetoothDeviceName = deviceName,
            ),
        )

        // Notify user
        val coordStr = "%.5f, %.5f".format(location.latitude, location.longitude)
        postNotification("Parking saved near $coordStr.")
    }

    private fun hasLocationPermission(): Boolean {
        return ContextCompat.checkSelfPermission(
            context,
            Manifest.permission.ACCESS_FINE_LOCATION,
        ) == PackageManager.PERMISSION_GRANTED
    }

    private fun getConfiguredCarNames(): List<String> {
        val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
        val raw = prefs.getString(PREF_CAR_BT_NAMES, "") ?: ""
        return raw.split(",").map { it.trim() }.filter { it.isNotEmpty() }
    }

    private fun postNotification(message: String) {
        try {
            val notification = NotificationCompat.Builder(
                context,
                NotificationPriority.ROUTINE.channelId,
            )
                .setSmallIcon(R.drawable.ic_launcher_foreground)
                .setContentTitle("Parking Saved")
                .setContentText(message)
                .setAutoCancel(true)
                .build()

            notificationManager.notify(NOTIFICATION_TAG, NOTIFICATION_ID, notification)
        } catch (e: Exception) {
            Log.w(TAG, "Failed to post parking notification: ${e.message}")
        }
    }

    companion object {
        private const val TAG = "ParkingMemory"
        private const val NOTIFICATION_TAG = "parking_memory"
        private const val NOTIFICATION_ID = 5002

        const val PREFS_NAME = "jarvis_prefs"
        const val PREF_CAR_BT_NAMES = "car_bluetooth_names"
    }
}
