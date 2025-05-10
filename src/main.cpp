/*
 * Garden-Cam · Arduino side · v5.3  (relay HIGH = Pi powered)
 * ------------------------------------------------------------
 * One constant – RELAY_ON_STATE – defines the polarity everywhere.
 * This version leaves it at HIGH, so the Pi receives power as soon
 * as the Arduino starts up when alwaysOn == true (default).
 */

 #include <Arduino.h>
 #include <Wire.h>
 #include <SPI.h>
 #include <LowPower.h>
 
 /* ─────────────  EDIT HERE IF POLARITY EVER CHANGES  ───────────── */
 #define RELAY_ON_STATE  HIGH        // HIGH = closed, LOW = open
 /* ──────────────────────────────────────────────────────────────── */
 
 #define PIN_RELAY  16
 #define PIN_PIR     3
 #define I2C_ADDR  0x08
 #define CMD_SHUT  0x07
 #define CMD_MODE  0x0D
 
 /* Battery cutoff (optional) */
 const float VREF = 5.0, R1 = 30000.0, R2 = 7500.0, VBAT_CUT = 5.40;
 bool  battCutoff = false;
 
 /* ──────────── state ───────────── */
 volatile bool alwaysOn   = true;      // keep Pi up by default
 volatile bool rqShutdown = false;
 
 bool  powering   = true;              // relay currently ON?
 unsigned long onMs = 0;
 bool  lastPir    = LOW;
 
 uint8_t battPct = 0;
 unsigned long battT = 0;
 
 /* ──────────── helpers ─────────── */
 inline void relayOn ()  { digitalWrite(PIN_RELAY, RELAY_ON_STATE      ); }
 inline void relayOff()  { digitalWrite(PIN_RELAY, !RELAY_ON_STATE     ); }
 inline bool relayIsOn(){ return digitalRead(PIN_RELAY)==RELAY_ON_STATE; }
 
 uint16_t readAdc() {
   ADCSRA |= _BV(ADEN);
   uint32_t s = 0; for(int i=0;i<8;i++) s += analogRead(A0);
   ADCSRA &= ~_BV(ADEN);
   return s>>3;
 }
 void saveBattery() {
   uint16_t raw = readAdc();
   float v = raw*VREF/1023.0*(R1+R2)/R2;
   battPct = constrain((uint8_t)((v-5.5)*100.0/3.3),0,100);
   if (battCutoff && v < VBAT_CUT) {
     Serial.println(F("Batt < 5.4 V → relay OFF + deep sleep"));
     relayOff();
     LowPower.powerDown(SLEEP_FOREVER, ADC_OFF, BOD_OFF);
   }
 }
 
 void applyRelay() {
   if (alwaysOn) {
     Serial.println(F("Mode: always-on  → relay HIGH (Pi ON)"));
     relayOn();  powering = true;  onMs = millis();
   } else {
     Serial.println(F("Mode: PIR        → relay LOW  (Pi OFF)"));
     relayOff(); powering = false;
   }
 }
 
 /* ───────── I²C ───────── */
 void rxEvent(int n) {
   if (!n) return;
   uint8_t cmd = Wire.read();
 
   if (cmd == CMD_MODE && n >= 2) {
     bool nm = Wire.read();
     if (nm != alwaysOn) { alwaysOn = nm; applyRelay(); }
   }
   else if (cmd == CMD_SHUT) rqShutdown = true;
 
   while (Wire.available()) Wire.read();
 }
 void txEvent() { Wire.write(battPct); }
 
 ISR(SPI_STC_vect) { SPDR = 0xFF; }   // dummy SPI
 
 /* ───────── setup ───────── */
 void setup() {
   Serial.begin(9600); while(!Serial);
   Serial.println(F("=== GardenCam Arduino v5.3 ==="));
   pinMode(PIN_RELAY, OUTPUT);
   pinMode(PIN_PIR,   INPUT_PULLUP);
 
   /* Power Pi immediately (alwaysOn default = true) */
   relayOn(); powering = true; onMs = millis();
 
   Wire.begin(I2C_ADDR);
   Wire.onReceive(rxEvent);
   Wire.onRequest(txEvent);
 
   lastPir = digitalRead(PIN_PIR);
   saveBattery(); battT = millis();
 }
 
 /* ───────── loop ───────── */
 void loop() {
   /* periodic battery check */
   if (millis() - battT >= 10000UL) { battT = millis(); saveBattery(); }
 
   /* PIR edge in PIR mode */
   bool pir = digitalRead(PIN_PIR);
   if (!alwaysOn && pir && !lastPir) {
     Serial.println(F("PIR ↑ → relay HIGH (60 s)"));
     relayOn(); powering=true; onMs=millis();
   }
   lastPir = pir;
 
   /* 60 s timeout */
   if (!alwaysOn && powering && millis() - onMs >= 60000UL) {
     Serial.println(F("60 s elapsed → relay LOW"));
     relayOff(); powering=false;
   }
 
   /* CMD_SHUT honoured only in PIR mode */
   if (!alwaysOn && rqShutdown) {
     Serial.println(F("CMD_SHUT → relay LOW"));
     relayOff(); powering=false; rqShutdown=false;
   }
 
   delay(50);
 }
 