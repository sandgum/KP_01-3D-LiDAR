#include <Arduino.h> 
#include <Wire.h>
#include <ESP32Servo.h>
#include <TFLI2C.h>
#include "driver/pcnt.h"
#include "esp_timer.h"
#include <freertos/FreeRTOS.h>
#include <freertos/semphr.h>

// Pin assignments for all peripherals

#define SCL_PIN 23
#define SDA_PIN 22
#define SAD_LED_PIN 21
#define HAPPY_LED_PIN 19
#define ENCODER_PIN 18
#define TILT_SERVO_PIN 5
#define PAN_MOTOR_PIN 17

// PCNT peripheral unit used
#define PCNT_UNIT_USED PCNT_UNIT_0
//number of notches per encoder wheel
#define NOTCHES_PER_WHEEL 10
// Tilt servo lower bound (degrees) (Higher angles point downwards)
#define TILT_SERVO_LOWER_BOUND 130
// Tilt servo upper bound (degrees) (Lower angles point upwards)
#define TILT_SERVO_UPPER_BOUND 40
// Step size (degrees) of servo
#define TILT_STEP_SIZE 10

// X coordinate of each point (cm)
float x;
// Y coordinate of each point (cm)
float y;
// Z coordinate of each point (cm)
float z;

// Raw distance readings from LiDAR (cm)
int16_t lidarDist;

// I2C address of TF Luna
const int lunaAddr = TFL_DEF_ADR;
// Frame rate of TF Luna (250fps is the maximum framerate supported)
uint16_t lunaFrameRate = 250;

// Total number of counted encoder pulses by PCNT
int16_t pulseCount = 0;
// Previous total number of counted encoder pulses by PCNT
int16_t pulseCountThen = 0;

// Microsecond timestamp of start of last rotation
uint64_t microsThen = 0;
// Previous microseconds per notch calculation (Used for out-of-bounds calculations)
float microsPerNotchThen = 0;
// Average interval in microseconds per notch on encoder wheel
float microsPerNotch = 0;
// Current calculated radians in this rotation (Ranges from 0 to 2PI)
float currRadians = 0;

// Enable low latency mode (Spins motor faster)
bool lowLatencyMode = false;

// Signal to start moving tilt servo (Triggered at the start of each rotation)
bool moveServo = false;
// Current angle written to tilt serevo (degrees)
int servoAngle = 90;
// If servo is moving upwards or downwards
bool servoUp = true;
// Dynamic servo upper bound (Used for emphasis)
uint8_t emphasisUpperBound = TILT_SERVO_UPPER_BOUND;
// Dynamic servo lower bound (Used for emphasis)
uint8_t emphasisLowerBound = TILT_SERVO_LOWER_BOUND;

// Data mutex for thread-safe access of data between pulseTimeTask and main loop
SemaphoreHandle_t dataMutex;

// TF Luna LiDAR object
TFLI2C luna;
// Servo object for tilt servo
Servo tiltServo;
// Servo object for pan motor
Servo panMotor;

// Function to initialise and attach I2C and GPIOs
void attachGPIOs() {

  Wire.begin(SDA_PIN, SCL_PIN);
  Serial.begin(115200);
  pinMode(SAD_LED_PIN, OUTPUT);
  pinMode(HAPPY_LED_PIN, OUTPUT);
  pinMode(ENCODER_PIN, INPUT_PULLUP);
}

// Function to initialise and set frame rate of TF Luna
void initialiseLuna() {

  if (luna.Set_Frame_Rate(lunaFrameRate, lunaAddr)) {
    digitalWrite(HAPPY_LED_PIN, HIGH);
  } else {
    digitalWrite(SAD_LED_PIN, HIGH);
  }
}

// Function to set up PCNT on encoder pin
void setupPCNT() {

  pcnt_config_t pcnt_config = {};
  pcnt_config.pulse_gpio_num = ENCODER_PIN;
  pcnt_config.ctrl_gpio_num = PCNT_PIN_NOT_USED;
  pcnt_config.channel = PCNT_CHANNEL_0;
  pcnt_config.unit = PCNT_UNIT_USED;

  // Count rising edges
  pcnt_config.pos_mode = PCNT_COUNT_INC;

  // Ignore falling edges
  pcnt_config.neg_mode = PCNT_COUNT_DIS;

  // No control pin (this is a simple on-off pulse counter)
  pcnt_config.lctrl_mode = PCNT_MODE_KEEP;
  pcnt_config.hctrl_mode = PCNT_MODE_KEEP;
  pcnt_config.counter_h_lim = 32767;
  pcnt_config.counter_l_lim = -32768;

  pcnt_unit_config(&pcnt_config);

  // Glitch filter for noisy signals (very good for stringy 3D printed encoder wheel)
  pcnt_set_filter_value(PCNT_UNIT_USED, 100);
  pcnt_filter_enable(PCNT_UNIT_USED);
  pcnt_counter_pause(PCNT_UNIT_USED);
  pcnt_counter_clear(PCNT_UNIT_USED);
  pcnt_counter_resume(PCNT_UNIT_USED);
}

// Function to attach and initialise servo objects
void servoSetup() {

  ESP32PWM::allocateTimer(0);
	ESP32PWM::allocateTimer(1);
	ESP32PWM::allocateTimer(2);
	ESP32PWM::allocateTimer(3);

  tiltServo.setPeriodHertz(50);
  panMotor.setPeriodHertz(50);
  tiltServo.attach(TILT_SERVO_PIN, 1000, 2000);
  panMotor.attach(PAN_MOTOR_PIN, 1000, 2000);

  // Init sequence for pan motor ESC and tilt servo
  delay(2000);
  tiltServo.write(90);
  panMotor.write(90);
  // Pan motor requires a period of time at output signal 90 for calibration
  delay(2000);
  // Full throttle on pan motor to overcome cogging and start rotation
  panMotor.write(180);
  delay(5000);
}

// Function to initialise pulseTimeTask on core 1 for accurate timestamps of each rotation
void initialiseTask() {

  // Create data mutex for thread-safe shared access to variables
  dataMutex = xSemaphoreCreateMutex();

  // Create FreeRTOS task pinned to core 1
  xTaskCreatePinnedToCore(
    pulseTimeTask,
    "pulseTimeTask",
    4096,
    NULL,
    1,
    NULL,
    1
  );
}

// Call all the above setup functions
void setup() {

  attachGPIOs();
  setupPCNT();
  initialiseLuna();
  servoSetup();
  initialiseTask();
}

// Repeatedly call motorController for servo control, and call readLuna for LiDAR distance, angle and coordinate calculations
// Then print x, y, z values separated by commas
void loop() {

  motorController();
  readLuna();
  Serial.printf("%f,%f,%f\n", x, y, z);
}

/* 
 This is an asynchronous task running as fast as it possibly can to accurately timestamp
 the exact microsecond value when a new rotation starts

 It constantly polls the PCNT counter for the number of notches counted
*/
void pulseTimeTask(void *pvParameters) {
  // Forever loop
  while (true) {
    // Poll PCNT for number of pulses
    pcnt_get_counter_value(PCNT_UNIT_USED, &pulseCount);

    // If a new rotation has started
    if ((pulseCount - pulseCountThen) >= NOTCHES_PER_WHEEL) {

      // Take data mutex (microsPerNotch, microsThen are being written)
      xSemaphoreTake(dataMutex, portMAX_DELAY);

      // Calculate average number of microseconds per notch using number of microseconds between rotations
      microsPerNotch = (float)((esp_timer_get_time() - microsThen) / (pulseCount - pulseCountThen));

      // Overvalue protection
      if (microsPerNotch > 100000) {
        microsPerNotch = microsPerNotchThen;
      }

      // Set "Then" values to  values now
      microsPerNotchThen = microsPerNotch;
      microsThen = esp_timer_get_time();
      moveServo = true;

      // Gove back data mutex to allow reads
      xSemaphoreGive(dataMutex);

      // Set pulseCountThen to new pulseCount
      pcnt_get_counter_value(PCNT_UNIT_USED, &pulseCount);
      pulseCountThen = pulseCount;
    }

  }
}

// Function to write to pan motor and control tilt servo
void motorController() {

  // If something has been typed in Serial
  if (Serial.available() > 0) {

    // Read the typed string until the line ending character
    String string = Serial.readStringUntil('\n');

    if (string == "clear") {
      // Set upper and lower bounds to defaults
      emphasisLowerBound = TILT_SERVO_LOWER_BOUND;
      emphasisUpperBound = TILT_SERVO_UPPER_BOUND;

    } else if (string == "bottom") {
      // Set upper bound to 20 degrees from lower bound
      emphasisLowerBound = TILT_SERVO_LOWER_BOUND;
      emphasisUpperBound = TILT_SERVO_LOWER_BOUND - 20;

    } else if (string == "middle") {
      // Set upper and lower bound 10 degrees symmetrically around 90 degrees
      emphasisLowerBound = 100;
      emphasisUpperBound = 80;

    } else if (string == "top") {
      // Set lower bound to 20 degrees from upper bound
      emphasisLowerBound = TILT_SERVO_UPPER_BOUND + 20;
      emphasisUpperBound = TILT_SERVO_UPPER_BOUND;

    } else if (string == "llm") {
      // Turn on low latency mode
      lowLatencyMode = true;

    } else if (string == "llmOff") {
      // Turn off low latency mode
      lowLatencyMode = false;
    }
  }

  if (lowLatencyMode) {
    // Write near-full throttle to pan motor
    panMotor.write(160);
  } else {
    // Write medium throttle to pan motor
    panMotor.write(110);
  }

  
  if (moveServo) {
    // Since higher angles correspond to lower tilt (It's in reverse), the logic is also reversed
    if (servoAngle >= emphasisLowerBound) {
      // Move servo down
      servoUp = false;
    } else if (servoAngle <= emphasisUpperBound) {
      // Move servo up
      servoUp = true;
    }

    if (servoUp) {
      // Add to servoAngle
      servoAngle += TILT_STEP_SIZE;
    } else {
      // Subtract from servoAngle
      servoAngle -= TILT_STEP_SIZE;
    }
    tiltServo.write(servoAngle);
    moveServo = false;
  }
}

// Function to read TF Luna's distance output, calculate radians of current rotation, and calculate point coordinates
void readLuna() {

  // Check if TF Luna is actually responding (To prevent null readings);
  if (luna.getData(lidarDist, lunaAddr)) {

    // Overvalue protection (Max range of TF Luna is 800cm)
    if (lidarDist > 800) lidarDist = 800;

    // Take data mutex (microsPerNotch and microsThen are being read)
    xSemaphoreTake(dataMutex, portMAX_DELAY);
    // Current radians calculation (Uses last measured microseconds per notch value and timestamp of last rotation start)
    currRadians = (2 * PI) * ((esp_timer_get_time() - microsThen) / (microsPerNotch * NOTCHES_PER_WHEEL));
    // Give back data mutex for pulseTimeTask to reac it again
    xSemaphoreGive(dataMutex);

    // X, Y and Z coordinates calculated using trigonometry (servoAngle is in degrees, so is converted ro radians)
    x = lidarDist * cos(currRadians);
    y = sin(currRadians) * lidarDist;
    z = sin((90 - servoAngle) * (PI / 180)) * lidarDist;

  } else {
    // The LiDAR is sad :(
    digitalWrite(SAD_LED_PIN, HIGH);
  }
}