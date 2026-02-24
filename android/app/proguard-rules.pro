# Jarvis Assistant ProGuard Rules

# Retrofit
-keepattributes Signature
-keepattributes *Annotation*
-keep class retrofit2.** { *; }
-keepclasseswithmembers class * {
    @retrofit2.http.* <methods>;
}

# Gson
-keep class com.jarvis.assistant.api.models.** { *; }
-keepclassmembers class * {
    @com.google.gson.annotations.SerializedName <fields>;
}

# Room entities
-keep class com.jarvis.assistant.data.entity.** { *; }

# SQLCipher
-keep class net.sqlcipher.** { *; }
