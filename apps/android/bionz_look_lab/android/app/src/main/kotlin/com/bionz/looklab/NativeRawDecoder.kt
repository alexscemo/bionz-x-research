package com.bionz.looklab

import java.io.File

data class NativeRawProxyResult(
    val rgbaBytes: ByteArray,
    val width: Int,
    val height: Int,
    val histRgb: ByteArray,
    val histW: Int,
    val histH: Int,
    val isLog2: Boolean,
    val isTrueRaw: Boolean
)

data class NativeRawLinearResult(
    val rgbFloats: FloatArray,
    val width: Int,
    val height: Int
)

/**
 * JNI bridge to libbionz_raw.so (14-bit Sony ARW 2.3 clean-room decoder).
 */
object NativeRawDecoder {

    val isAvailable: Boolean = try {
        System.loadLibrary("bionz_raw")
        true
    } catch (_: Throwable) {
        false
    }

    fun decodeProxyFromFile(file: File, maxDim: Int = 1600): NativeRawProxyResult? {
        if (!isAvailable || !file.isFile) return null
        return try {
            @Suppress("UNCHECKED_CAST")
            val map = nativeDecodeArwProxy(file.absolutePath, -1, maxDim) as? Map<String, Any?>
                ?: return null
            parseProxyMap(map)
        } catch (_: Throwable) {
            null
        }
    }

    fun decodeProxyFromFd(fd: Int, maxDim: Int = 1600): NativeRawProxyResult? {
        if (!isAvailable || fd < 0) return null
        return try {
            @Suppress("UNCHECKED_CAST")
            val map = nativeDecodeArwProxy(null, fd, maxDim) as? Map<String, Any?>
                ?: return null
            parseProxyMap(map)
        } catch (_: Throwable) {
            null
        }
    }

    fun decodeFullResLinearFromFile(file: File, maxDim: Int = 3000): NativeRawLinearResult? {
        if (!isAvailable || !file.isFile) return null
        return try {
            @Suppress("UNCHECKED_CAST")
            val map = nativeDecodeArwFullResLinear(file.absolutePath, -1, maxDim) as? Map<String, Any?>
                ?: return null
            parseLinearMap(map)
        } catch (_: Throwable) {
            null
        }
    }

    fun decodeFullResLinearFromFd(fd: Int, maxDim: Int = 3000): NativeRawLinearResult? {
        if (!isAvailable || fd < 0) return null
        return try {
            @Suppress("UNCHECKED_CAST")
            val map = nativeDecodeArwFullResLinear(null, fd, maxDim) as? Map<String, Any?>
                ?: return null
            parseLinearMap(map)
        } catch (_: Throwable) {
            null
        }
    }

    private fun parseProxyMap(map: Map<String, Any?>): NativeRawProxyResult? {
        val rgba = map["rgba"] as? ByteArray ?: return null
        val w = (map["width"] as? Number)?.toInt() ?: 0
        val h = (map["height"] as? Number)?.toInt() ?: 0
        if (w <= 0 || h <= 0 || rgba.size < w * h * 4) return null
        val histRgb = map["histRgb"] as? ByteArray ?: ByteArray(0)
        val histW = (map["histW"] as? Number)?.toInt() ?: 0
        val histH = (map["histH"] as? Number)?.toInt() ?: 0
        val isLog2 = (map["isLog2"] as? Boolean) ?: true
        val isTrueRaw = (map["isTrueRaw"] as? Boolean) ?: true
        return NativeRawProxyResult(
            rgbaBytes = rgba,
            width = w,
            height = h,
            histRgb = histRgb,
            histW = histW,
            histH = histH,
            isLog2 = isLog2,
            isTrueRaw = isTrueRaw
        )
    }

    private fun parseLinearMap(map: Map<String, Any?>): NativeRawLinearResult? {
        val rgb = map["rgb"] as? FloatArray ?: return null
        val w = (map["width"] as? Number)?.toInt() ?: 0
        val h = (map["height"] as? Number)?.toInt() ?: 0
        if (w <= 0 || h <= 0 || rgb.size < w * h * 3) return null
        return NativeRawLinearResult(rgbFloats = rgb, width = w, height = h)
    }

    @JvmStatic
    private external fun nativeDecodeArwProxy(path: String?, fd: Int, maxDim: Int): Any?

    @JvmStatic
    private external fun nativeDecodeArwFullResLinear(path: String?, fd: Int, maxDim: Int): Any?
}
