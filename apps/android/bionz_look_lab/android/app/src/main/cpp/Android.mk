LOCAL_PATH := $(call my-dir)

include $(CLEAR_VARS)
LOCAL_MODULE := bionz_raw
LOCAL_SRC_FILES := arw_decoder.cpp
LOCAL_CPPFLAGS := -std=c++17 -O3 -ffast-math -fvisibility=hidden
LOCAL_LDLIBS := -llog
include $(BUILD_SHARED_LIBRARY)
