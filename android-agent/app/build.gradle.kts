plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.example.douyinagent"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.example.douyinagent"
        minSdk = 26
        targetSdk = 34
        versionCode = 10
        versionName = "0.8.4"
    }

    buildFeatures {
        buildConfig = true
    }

    kotlinOptions {
        jvmTarget = "1.8"
    }
}

dependencies {}
