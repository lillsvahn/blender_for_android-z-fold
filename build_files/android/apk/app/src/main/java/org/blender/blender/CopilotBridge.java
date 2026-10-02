/* SPDX-FileCopyrightText: 2026 Blender Authors
 * SPDX-License-Identifier: GPL-2.0-or-later */
package org.blender.blender;

import android.app.AlertDialog;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.text.InputType;
import android.util.AtomicFile;
import android.util.Base64;
import android.widget.EditText;
import org.json.JSONArray;
import org.json.JSONObject;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.net.ssl.HttpsURLConnection;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.time.ZonedDateTime;
import java.time.format.DateTimeFormatter;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;

/** Platform transport and secret storage. No bpy, tools, model loops or retries.
 * Only this class sees the key, only as a request header. Error messages deliberately
 * omit exception/server bodies, which can echo credentials or request contents. */
final class CopilotBridge {
  private static final String ALIAS = "blender.gemini.v1";
  private final BlenderActivity activity;
  private final AtomicFile keyFile;
  private final ExecutorService executor = Executors.newSingleThreadExecutor();
  private final ExecutorService canceller = Executors.newSingleThreadExecutor();
  private Future<?> future;
  private volatile HttpsURLConnection connection;
  private long generation = 0;
  private long cooldownUntil = 0;
  private boolean pending = false;
  private boolean paused = false;
  private JSONObject result;

  CopilotBridge(BlenderActivity activity) {
    this.activity = activity;
    keyFile = new AtomicFile(new File(activity.getNoBackupFilesDir(), "gemini-key.enc"));
  }

  private SecretKey encryptionKey() throws Exception {
    KeyStore store = KeyStore.getInstance("AndroidKeyStore");
    store.load(null);
    if (!store.containsAlias(ALIAS)) {
      KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,
                                                        "AndroidKeyStore");
      generator.init(new KeyGenParameterSpec.Builder(ALIAS,
          KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
          .setKeySize(256).setBlockModes(KeyProperties.BLOCK_MODE_GCM)
          .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
      generator.generateKey();
    }
    return (SecretKey)store.getKey(ALIAS, null);
  }

  private synchronized void saveKey(String key) throws Exception {
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(Cipher.ENCRYPT_MODE, encryptionKey());
    JSONObject encrypted = new JSONObject();
    encrypted.put("iv", Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP));
    encrypted.put("data", Base64.encodeToString(cipher.doFinal(
        key.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP));
    FileOutputStream out = keyFile.startWrite();
    try {
      out.write(encrypted.toString().getBytes(StandardCharsets.UTF_8));
      keyFile.finishWrite(out);
    }
    catch (Exception ex) { keyFile.failWrite(out); throw ex; }
  }

  private synchronized String readKey() throws Exception {
    JSONObject encrypted = new JSONObject(new String(keyFile.readFully(), StandardCharsets.UTF_8));
    Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
    cipher.init(Cipher.DECRYPT_MODE, encryptionKey(), new GCMParameterSpec(128,
        Base64.decode(encrypted.getString("iv"), Base64.NO_WRAP)));
    return new String(cipher.doFinal(Base64.decode(encrypted.getString("data"), Base64.NO_WRAP)),
                      StandardCharsets.UTF_8);
  }

  private void configureKey() {
    activity.runOnUiThread(() -> {
      EditText edit = new EditText(activity);
      edit.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
      edit.setSingleLine(true);
      // Never prefill, expose to Python, or put the value in an Intent/log.
      new AlertDialog.Builder(activity).setTitle("Gemini API key").setView(edit)
          .setPositiveButton("Save", (dialog, which) -> {
            String key = edit.getText().toString().trim();
            edit.getText().clear();
            if (key.isEmpty() || key.length() > 512 || key.contains("\n") || key.contains("\r")) {
              new AlertDialog.Builder(activity).setMessage("Invalid API key").setPositiveButton("OK", null).show();
              return;
            }
            try { saveKey(key); }
            catch (Exception ex) {
              new AlertDialog.Builder(activity).setMessage("Unable to save key in Android Keystore")
                  .setPositiveButton("OK", null).show();
            }
          }).setNeutralButton("Clear", (dialog, which) -> {
            edit.getText().clear();
            synchronized (this) { stop(); keyFile.delete(); }
          }).setNegativeButton("Cancel", (dialog, which) -> edit.getText().clear()).show();
    });
  }

  private static JSONObject message(boolean ok, String error) {
    JSONObject value = new JSONObject();
    try { value.put("ok", ok); if (error != null) { value.put("error", error); } }
    catch (Exception ignored) { }
    return value;
  }
  private static String ascii(JSONObject value) {
    String source = value.toString();
    StringBuilder out = new StringBuilder(source.length());
    for (int i = 0; i < source.length(); i++) {
      char c = source.charAt(i);
      if (c >= 128) { out.append(String.format(java.util.Locale.ROOT, "\\u%04x", (int)c)); }
      else { out.append(c); }
    }
    return out.toString();
  }

  synchronized String call(String action, String payload) {
    try {
      JSONObject reply = message(true, null);
      switch (action) {
        case "configure_key": configureKey(); break;
        case "status":
          reply.put("configured", keyFile.getBaseFile().isFile());
          reply.put("pending", pending);
          reply.put("retry_after", Math.max(0, (cooldownUntil - System.currentTimeMillis() + 999) / 1000));
          break;
        case "stop": stop(); break;
        case "poll":
          if (result != null) { reply = result; result = null; }
          else { reply.put("pending", pending); }
          break;
        case "request": {
          if (paused) { return ascii(message(false, "Android activity is paused")); }
          if (pending) { return ascii(message(false, "A request is already running")); }
          if (System.currentTimeMillis() < cooldownUntil) {
            reply = message(false, "Gemini quota/rate limit; try again later");
            reply.put("http_status", 429);
            reply.put("retry_after", (cooldownUntil - System.currentTimeMillis() + 999) / 1000);
            return ascii(reply);
          }
          JSONObject request = new JSONObject(payload);
          String model = request.getString("model");
          if (!model.matches("[A-Za-z0-9._-]{1,128}")) {
            return ascii(message(false, "Set a valid Gemini model ID"));
          }
          String body = request.getJSONObject("body").toString();
          if (body.getBytes(StandardCharsets.UTF_8).length > 12 * 1024 * 1024) {
            return ascii(message(false, "Request too large"));
          }
          if (!keyFile.getBaseFile().isFile()) { return ascii(message(false, "Configure an API key first")); }
          pending = true; result = null;
          long ticket = ++generation;
          future = executor.submit(() -> request(ticket, model, body));
          reply.put("pending", true);
          break;
        }
        default: reply = message(false, "Unknown Android bridge action");
      }
      return ascii(reply);
    }
    catch (Exception ex) { return ascii(message(false, "Android bridge error")); }
  }

  private void request(long ticket, String model, String body) {
    HttpsURLConnection conn = null;
    JSONObject reply;
    long retry = 0;
    try {
      String key = readKey();
      conn = (HttpsURLConnection)new URL("https://generativelanguage.googleapis.com/v1beta/models/"
                                        + model + ":generateContent").openConnection();
      synchronized (this) {
        if (ticket != generation || paused) { return; }
        connection = conn;
      }
      conn.setInstanceFollowRedirects(false);
      conn.setRequestMethod("POST"); conn.setDoOutput(true);
      conn.setConnectTimeout(15000); conn.setReadTimeout(90000);
      conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
      conn.setRequestProperty("x-goog-api-key", key);
      byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
      conn.setFixedLengthStreamingMode(bytes.length);
      try (java.io.OutputStream out = conn.getOutputStream()) { out.write(bytes); }
      int status = conn.getResponseCode();
      JSONObject data = null;
      InputStream stream = status < 400 ? conn.getInputStream() : conn.getErrorStream();
      if (stream != null) {
        try (InputStream in = stream; ByteArrayOutputStream out = new ByteArrayOutputStream()) {
          byte[] buffer = new byte[16384]; int n;
          while ((n = in.read(buffer)) != -1) {
            if (out.size() + n > 2 * 1024 * 1024) { throw new Exception("size"); }
            out.write(buffer, 0, n);
          }
          try { data = new JSONObject(new String(out.toByteArray(), StandardCharsets.UTF_8)); }
          catch (Exception ignored) { }
        }
      }
      boolean exhausted = data != null && data.optJSONObject("error") != null &&
          "RESOURCE_EXHAUSTED".equals(data.getJSONObject("error").optString("status"));
      if (status == 429 || exhausted) {
        retry = retrySeconds(conn.getHeaderField("Retry-After"), data);
        reply = message(false, "Gemini quota/rate limit; no automatic retry");
        reply.put("http_status", 429); reply.put("retry_after", retry);
      }
      else if (status >= 200 && status < 300 && data != null) {
        reply = message(true, null); reply.put("response", data); reply.put("http_status", status);
      }
      else {
        reply = message(false, "Gemini API returned HTTP " + status);
        reply.put("http_status", status);
      }
    }
    catch (Exception ex) { reply = message(false, "Network, TLS or key-storage error; try again later"); }
    finally { if (conn != null) { conn.disconnect(); } }
    synchronized (this) {
      if (ticket == generation) {
        if (retry > 0) { cooldownUntil = System.currentTimeMillis() + retry * 1000; }
        pending = false; connection = null; result = reply;
      }
    }
  }

  private static long retrySeconds(String header, JSONObject data) {
    long seconds = 60;
    try {
      if (header != null) {
        try { seconds = Long.parseLong(header); }
        catch (NumberFormatException ex) {
          seconds = (ZonedDateTime.parse(header, DateTimeFormatter.RFC_1123_DATE_TIME)
                         .toInstant().toEpochMilli() - System.currentTimeMillis() + 999) / 1000;
        }
      }
      if (data != null && data.optJSONObject("error") != null) {
        JSONArray details = data.getJSONObject("error").optJSONArray("details");
        if (details != null) {
          for (int i = 0; i < details.length(); i++) {
            JSONObject item = details.optJSONObject(i);
            if (item == null) { continue; }
            String delay = item.optString("retryDelay");
            if (delay.endsWith("s")) {
              seconds = Math.max(seconds, (long)Math.ceil(Double.parseDouble(delay.substring(0, delay.length() - 1))));
            }
          }
        }
      }
    }
    catch (Exception ignored) { }
    return Math.max(1, Math.min(seconds, (Long.MAX_VALUE - System.currentTimeMillis()) / 1000));
  }
  private synchronized void stop() {
    generation++; pending = false; result = null;
    if (future != null) { future.cancel(true); future = null; }
    HttpsURLConnection conn = connection; connection = null;
    // disconnect can wait on an internal network lock. Never wait on it from
    // Blender's main thread or Android's lifecycle/UI thread.
    if (conn != null) { canceller.execute(conn::disconnect); }
  }
  synchronized void pause() { paused = true; stop(); }
  synchronized void resume() { paused = false; }
  synchronized void close() { pause(); executor.shutdownNow(); canceller.shutdown(); }
}
