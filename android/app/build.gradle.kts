import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Local signing key (made by tools/make-key.sh). Not a Play key; only for sideloading.
val keyProps = Properties().apply {
    val f = rootProject.file("keystore/key.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
// The server the app talks to by default (it can be changed in the app's settings): tvframe.server in local.properties.
val localProps = Properties().apply {
    val f = rootProject.file("local.properties")
    if (f.exists()) f.inputStream().use { load(it) }
}
val tvframeServer: String = (findProperty("tvframe.server") as String?) ?: localProps.getProperty("tvframe.server", "http://tvframe.local")
// The home's time zone, for when the TV's own zone is set wrong. Empty means the device zone.
val tvframeTz: String = (findProperty("tvframe.tz") as String?) ?: localProps.getProperty("tvframe.tz", "")

android {
    namespace = "app.tvframe"
    compileSdk = 34
    buildToolsVersion = "35.0.0"

    defaultConfig {
        applicationId = "app.tvframe"
        minSdk = 31
        targetSdk = 34
        versionCode = 13
        versionName = "0.3.7"
        buildConfigField("String", "TVFRAME_SERVER", "\"$tvframeServer\"")
        buildConfigField("String", "TVFRAME_TZ", "\"$tvframeTz\"")
    }

    signingConfigs {
        create("local") {
            storeFile = rootProject.file(keyProps.getProperty("storeFile", "keystore/tvframe-local.jks"))
            storePassword = keyProps.getProperty("storePassword")
            keyAlias = keyProps.getProperty("keyAlias")
            keyPassword = keyProps.getProperty("keyPassword")
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("local")
        }
        debug {
            signingConfig = signingConfigs.getByName("local")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
    buildFeatures {
        buildConfig = true
        resValues = false
    }
    dependenciesInfo {
        includeInApk = false
        includeInBundle = false
    }
    packaging {
        resources.excludes += listOf("kotlin/**", "META-INF/*.version", "META-INF/**/*.kotlin_module",
            "DebugProbesKt.bin", "kotlin-tooling-metadata.json")
    }
    lint {
        checkReleaseBuilds = false
    }
}

// No AndroidX, no Play Services, no network or analytics libraries: framework APIs only.
dependencies {}
