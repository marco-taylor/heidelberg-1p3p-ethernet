#!/usr/bin/env python3
"""Host fault injection into the actual candidate Ethernet firmware functions.

No device access. GPIO, RTU, clock and restart are simulated. No expected failures are allowed. This is not a hardware safety approval. See docs/HEIDELBERG_SAFETY_AUDIT.md.
"""

import argparse
import hashlib
from pathlib import Path
import re
import subprocess
import tempfile
import unittest



def extract(source, name):
    """Keep original function text, balancing braces outside comments/literals."""
    masked = re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'',
                    lambda m: " " * len(m.group()), source, flags=re.S)
    match = re.search(r"^[^\n;{}]*\b" + re.escape(name) + r"\([^;{}]*\)\s*\{", masked, re.M)
    if match is None:
        raise ValueError("function missing: " + name)
    opening = masked.index("{", match.start())
    depth = 1
    end = opening + 1
    while depth:
        depth += (masked[end] == "{") - (masked[end] == "}")
        end += 1
    return source[match.start():end] + "\n"


PRELUDE = r'''
#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <iostream>
#include <mutex>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>
#define ESP_LOGI(...) ((void)0)
#define ESP_LOGW(...) ((void)0)
#define ESP_LOGE(...) ((void)0)
#define RTC_NOINIT_ATTR
constexpr int ESP_RST_SW=1, ESP_RST_BROWNOUT=2, ESP_RST_PANIC=3, ESP_RST_TASK_WDT=4;
int resetReason=ESP_RST_SW;
int esp_reset_reason(){return resetReason;}
// CRC is an environmental stub: cases test the reset-reason gate, not ROM CRC.
uint32_t esp_rom_crc32_le(uint32_t crc,const uint8_t* bytes,size_t length){
  for(size_t i=0;i<length;i++)crc=(crc*33)^bytes[i];
  return crc;
}
constexpr int ESP_OK=0, PIN_1P_IN=36, PIN_3P_IN=39, PIN_1P_OUT=16, PIN_3P_OUT=2;
constexpr int RELAY_OFF=0, RELAY_ON=1, HEC_REG_MAX_CURRENT=261, HEC_REG_REMOTE_LOCK=259;
constexpr int HEC_REG_WATCHDOG=257, HEC_REG_FAILSAFE_CURRENT=262, HEC_MIN_CURRENT=60;
constexpr int HOLDING_REG_OFFSET=256;
uint32_t clockMs=1001;
std::array<int,40> pins{};
std::vector<std::pair<int,int>> outputs;
uint32_t nowMs(){return clockMs;}
int readPin(int pin){return pins.at(pin);}
void writePin(int pin,int value){pins.at(pin)=value; outputs.emplace_back(pin,value);}
void cycleCounterContactorOn(int){}
enum class EventType {SafetyFault, EvccTimeout};
void eventLog(EventType,int,int,int,uint32_t){}
enum class SwitchSource {Evcc, WebPage};
struct FeedbackDebounce {bool active=false; uint32_t sinceMs=0;};
enum class State {AwaitingStart, Running, SwitchPhases, WaitingForZero, ConfirmedZero, WaitingForOff,
  ConfirmedOff, SwitchedOn, Delay, Fault, Unlocking, Restarting};
struct Restart {};
class PhaseSwitch {
public:
  std::mutex _stateMutex, _cacheMutex, _evccMutex;
  State _state=State::AwaitingStart;
  std::atomic<uint8_t> _desiredPhases{1}, _requestedPhases{0}, _confirmedPhases{1};
  std::atomic<uint8_t> _lastSwitchFrom{1}, _lastSwitchTo{1}, _prevSwitchFrom{1}, _prevSwitchTo{1}, _switchSource{0};
  uint32_t _switchStartMs=0;
  std::atomic<uint16_t> _safetyFaultCode{0};
  std::atomic<const char*> _safetyFaultText{""};
  bool _restartRequested=false, _tcpServerStarted=true, _tcpBridgeWanted=true;
  bool _switchingSupported=true, _firmwareSupported=true, _master_handle=true;
  bool _wallboxSilent=false, _switchOnAnswered=false, _phaseSwitchRequested=false;
  bool _currentWrittenDuringSwitch=false, _restartStopWritten=false, _carQuiet=false;
  bool _evccTimeout=false, _masterStarted=true, _updateInProgress=false;
  bool _restartStopConfirmed=false, _restartResumeAllowed=false;
  uint32_t _updateCurrentWritesSeen=0, _updateLockWritesSeen=0, _cachedLockWrites=0;
  FeedbackDebounce _offFeedback, _bothFeedbackSinceMs, _invalidFeedbackSinceMs;
  uint32_t _previous=0, _delay=0, _tcpBridgeLastTryMs=0;
  uint32_t _switchOffDeadlineMs=0, _switchOnDeadlineMs=0;
  uint32_t _wallboxResponseDeadlineMs=0, _zeroLoadDeadlineMs=0, _switchDelay=120000;
  uint32_t _unlockDeadlineMs=0, _lastWallboxOkMs=0, _carQuietUntilMs=0;
  uint32_t _restartDeadlineMs=0, _cachedCurrentWrites=0, _evccTimeouts=0;
  std::atomic<uint32_t> _lastBridgeRequestMs{0};
  std::vector<uint16_t> _holdingRegister=std::vector<uint16_t>(10);
  std::vector<uint16_t> _inputRegister=std::vector<uint16_t>(20);
  uint16_t _reportedStatus=4;
  bool _bootCacheValid=false;
  uint32_t _switchCount=0, _wallboxOutages=0;
  std::array<uint16_t,4> sample{{7,100,100,100}};
  std::array<uint16_t,3> voltages{{230,230,230}};
  std::vector<std::pair<int,int>> writes;
  bool readOK=true, zeroWriteOK=true, lockWriteOK=true, setupOK=true;
  bool silentOnCheck=false;
  int restarts=0;
  bool injectBootCurrent=false;
  int masterWriteHolding(uint16_t reg,uint16_t value){
    writes.emplace_back(reg,value);
    if(injectBootCurrent && reg==261){
      injectBootCurrent=false;
      _holdingRegister[261-256]=160;
      _cachedCurrentWrites++;
    }
    return (reg==261 ? zeroWriteOK : lockWriteOK) ? ESP_OK : -1;
  }
  int masterReadInput(uint16_t reg,uint16_t count,uint16_t* dest){
    if(!readOK) return -1;
    if(reg==10){std::copy_n(voltages.begin(),count,dest);}
    else {std::copy_n(sample.begin(),count,dest);}
    return ESP_OK;
  }
  uint16_t getHoldingRegister(uint16_t reg){return _holdingRegister.at(reg-256);}
  void cacheHolding(uint16_t reg,const uint16_t* v,uint16_t){_holdingRegister.at(reg-256)=*v;}
  void rememberCarState(){}
  void startTcpBridge(){}
  bool validateSetup(){return setupOK;}
  void checkWallboxSilent(){_wallboxSilent=silentOnCheck;}
  void logRefusedConnections(bool){}
  bool updateCachedRegisters(){return false;}
  void logCarState(){}
  void finishSwitchStats(){}
  void abortSwitch(){_state=State::Running;}
  void startSwitch(uint8_t,uint8_t);
  bool requestPhases(uint8_t); bool canSwitchTo1P(); bool canSwitchTo3P();
  [[noreturn]] void restartNow(){saveBootCache(); restarts++; throw Restart{};}
  void loop(); void handleRestart(); bool writeRestartStop();
  void enterSafetyFault(uint16_t,const char*); bool hasSafetyFault();
  uint8_t reportedPhases(); bool readActivePhases(uint8_t&); void checkEvccWatchdog();
  void loadBootCache(); void saveBootCache(); void requestRestart();
  bool feedbackMatches(uint8_t); bool outputsConfirmedOff(); bool prepareUpdate();
  void setUpdateInProgress(bool);
};
struct esp_timer_create_args_t {void (*callback)(void*)=nullptr; const char* name=nullptr;};
using esp_timer_handle_t=void*;
PhaseSwitch* s_phase_switch=nullptr;
esp_timer_handle_t s_restart_timer=nullptr;
bool timerCreateOK=true, timerStartOK=true;
int timerToken=0;
void (*timerCallback)(void*)=nullptr;
int esp_timer_create(const esp_timer_create_args_t* args,esp_timer_handle_t* handle){
  if(!timerCreateOK)return -1;
  timerCallback=args->callback; *handle=&timerToken; return 0;
}
int esp_timer_stop(esp_timer_handle_t){return 0;}
int esp_timer_start_once(esp_timer_handle_t,uint64_t){return timerStartOK ? 0 : -1;}
'''

CASES = r'''
void require(bool condition,const char* message){if(!condition)throw std::runtime_error(message);}
int main(int argc,char**argv){try{
  if(argc!=2)return 2;
  PhaseSwitch p;
  std::string test=argv[1];
  if(test=="boot_concurrent_current"){
    p._state=State::Delay; p.injectBootCurrent=true;
    pins[PIN_1P_IN]=1; pins[PIN_1P_OUT]=1;
    p.loop();
    require(p._state==State::Unlocking,"boot handover did not complete");
    for(auto write:p.writes) require(write.first!=261 || write.second==0,"boot restored concurrent nonzero current");
    return 0;
  }
  pins.fill(0); pins[PIN_1P_IN]=1;
  p._state=State::Running;
  if(test=="restart_waits_for_zero"){
    p.handleRestart(); require(p.restarts==0 && outputs.empty(),"restart before stop confirmation");
  }else if(test=="restart_confirmed"){
    p.handleRestart(); p.sample={10,0,0,0}; clockMs+=501;
    try{p.handleRestart();}catch(Restart&){}
    require(p.restarts==1,"confirmed stop did not restart");
  }else if(test=="restart_timeout"){
    p.handleRestart(); clockMs+=10001;
    try{p.handleRestart();}catch(Restart&){}
    require(p.restarts==0,"restart occurred despite nonzero current");
  }else if(test=="restart_read_error"){
    p.handleRestart(); p.readOK=false; clockMs+=10001;
    try{p.handleRestart();}catch(Restart&){}
    require(p.restarts==0,"restart occurred without readable current");
  }else if(test=="restart_lock_error"){
    p.lockWriteOK=false;
    require(!p.writeRestartStop(),"failed remote-lock write reported successful");
  }else if(test=="debounce_even"){
    clockMs=1000; FeedbackDebounce since;
    require(!persistsFor(true,since,100),"100 ms debounce accepted zero elapsed time");
  }else if(test=="debounce_normal"){
    FeedbackDebounce since;
    require(!persistsFor(true,since,100),"premature debounce");
    clockMs+=100; require(persistsFor(true,since,100),"missing debounce");
  }else if(test=="wrong_feedback"){
    p._desiredPhases=3; p.loop(); clockMs+=101; p.loop();
    require(p.hasSafetyFault(),"opposite single feedback accepted while Running");
  }else if(test=="missing_feedback"){
    pins[PIN_1P_IN]=0; p.loop(); clockMs+=101; p.loop();
    require(p._safetyFaultCode==0xE103 && outputs.size()==2,"missing feedback not latched");
  }else if(test=="both_feedback"){
    pins[PIN_3P_IN]=1; p.loop(); clockMs+=101; p.loop();
    require(p._safetyFaultCode==0xE101 && outputs.size()==2,"both feedback not latched");
  }else if(test=="fault_latched"){
    p.enterSafetyFault(0xE101,"injected"); auto n=outputs.size();
    pins[PIN_1P_IN]=0; p.loop();
    require(p._state==State::Fault && outputs.size()==n,"fault recovered without reset");
  }else if(test=="stop_write_error"){
    p._state=State::SwitchPhases; p.zeroWriteOK=false; p.loop();
    require(outputs.empty() && p._state==State::SwitchPhases,"switch despite failed stop");
  }else if(test=="nonzero_current"){
    p._state=State::WaitingForZero; p._zeroLoadDeadlineMs=clockMs-1; p.loop();
    require(outputs.empty() && p._state==State::WaitingForZero,"switch under nonzero current");
  }else if(test=="zero_read_error"){
    p._state=State::WaitingForZero; p.readOK=false; p.loop();
    require(outputs.empty(),"switch without a current reading");
  }else if(test=="zero_confirmed"){
    p._state=State::WaitingForZero; p.sample={10,0,0,0}; p.loop();
    require(p._state==State::ConfirmedZero && outputs.empty(),"missing stop confirmation");
    p.loop(); require(p._state==State::WaitingForOff && outputs.size()==2,"missing output-off");
  }else if(test=="stuck_contactor"){
    p._state=State::WaitingForOff; p.loop(); clockMs+=8001; p.loop();
    require(p._safetyFaultCode==0xE105,"stuck feedback not latched");
  }else if(test=="off_deadtime"){
    pins[PIN_1P_IN]=0; p._state=State::WaitingForOff; p.loop();
    clockMs+=100; p.loop();
    clockMs+=1999; p.loop(); require(outputs.empty(),"energized before off deadtime");
    clockMs+=1; p.loop(); require(outputs.size()==1,"missing delayed energize");
  }else if(test=="boot_without_request"){
    pins[PIN_1P_IN]=0; p._state=State::AwaitingStart; p._confirmedPhases=0; p.loop(); clockMs+=2000; p.loop();
    require(outputs.empty(),"contactor energized without an external request");
  }else if(test=="boot_current_zero"){
    p._state=State::Delay; p._holdingRegister[5]=160; p.loop();
    require(p.writes.size()>=2 && p.writes[0]==std::make_pair(261,0)
      && p.writes[1]==std::make_pair(259,1),"boot did not restore 0 A before unlock");
  }else if(test=="switch_old_current_capped"){
    p._state=State::Delay; p._phaseSwitchRequested=true; p._holdingRegister[5]=160; p.loop();
    require(p.writes[0]==std::make_pair(261,60),"old current not capped to 6 A");
  }else if(test=="unlock_requires_confirmation"){
    p._state=State::Unlocking; p.readOK=false; p.lockWriteOK=false;
    p._unlockDeadlineMs=clockMs-1; p.loop();
    require(p._state!=State::Running,"Running despite failed unlock/readback");
  }else if(test=="phase_report_is_confirmation"){
    p._state=State::WaitingForZero; p._desiredPhases=3;
    require(p.reportedPhases()==1,"register reports requested 3P while feedback is 1P");
  }else if(test=="voltage_rejects_mixed"){
    uint8_t phases=255; p.voltages={230,230,0};
    require(p.readActivePhases(phases) && phases==0,"mixed voltages reported as valid phases");
  }else if(test=="watchdog_zero"){
    p._holdingRegister[1]=100; p._holdingRegister[6]=0;
    p._lastBridgeRequestMs=1; p.checkEvccWatchdog();
    require(p.writes.size()==1 && p.writes[0]==std::make_pair(261,0),"watchdog did not apply 0 A");
  }else if(test=="software_cache" || test=="brownout_cache" || test=="panic_cache"){
    p._state=State::AwaitingStart; p._confirmedPhases=0;
    s_bootCache={}; s_bootCache.magic=BOOT_CACHE_MAGIC; s_bootCache.desiredPhases=3;
    s_bootCache.crc=bootCacheCrc(s_bootCache);
    resetReason=test=="software_cache" ? ESP_RST_SW : (test=="brownout_cache" ? ESP_RST_BROWNOUT : ESP_RST_PANIC);
    p.loadBootCache();
    require(p._bootCacheValid==(resetReason==ESP_RST_SW),"reset reason gate changed");
    require(p._desiredPhases==(resetReason==ESP_RST_SW ? 3 : 1),"reset phase selection changed");
  }else if(test=="watchdog_nonzero"){
    p._holdingRegister[1]=100; p._holdingRegister[6]=160;
    p._lastBridgeRequestMs=1; p.checkEvccWatchdog();
    require(p.writes.size()==1 && p.writes[0]==std::make_pair(261,160),"failsafe policy changed");
  }else if(test=="restart_late_stop" || test=="restart_during_switch" || test=="restart_during_unlock"){
    if(test=="restart_during_switch"){p._state=State::WaitingForZero;p._desiredPhases=3;}
    if(test=="restart_during_unlock")p._state=State::Unlocking;
    p.handleRestart();clockMs+=10001;p.handleRestart();
    require(p.restarts==0 && outputs.empty(),"restart dropped outputs under load");
    p.sample={10,0,0,0};clockMs+=501;
    try{p.handleRestart();}catch(Restart&){}
    require(p.restarts==1 && s_bootCache.desiredPhases==1,"unsafe restart target or missing restart");
  }else if(test=="restart_current_L2" || test=="restart_current_L3" || test=="restart_unlocked_zero"){
    p.sample={10,0,0,0};
    if(test=="restart_current_L2")p.sample[2]=100;
    if(test=="restart_current_L3")p.sample[3]=100;
    if(test=="restart_unlocked_zero")p.sample[0]=4;
    p.handleRestart();clockMs+=20001;p.handleRestart();
    require(p.restarts==0 && outputs.empty(),"restart accepted incomplete stop confirmation");
  }else if(test=="restart_lock_retry"){
    p.lockWriteOK=false;p.handleRestart();p.lockWriteOK=true;clockMs+=501;p.handleRestart();
    require(std::count(p.writes.begin(),p.writes.end(),std::make_pair(259,0))==2,"lock was not retried");
    require(p.restarts==0,"restart before zero current");
  }else if(test=="restart_missing_master"){
    p._master_handle=false;p.handleRestart();clockMs+=20001;p.handleRestart();
    require(p.restarts==0 && outputs.empty(),"restart without master dropped load");
  }else if(test=="restart_already_off"){
    pins[PIN_1P_IN]=0;p._confirmedPhases=0;p._state=State::AwaitingStart;p._master_handle=false;
    p.handleRestart();clockMs+=501;try{p.handleRestart();}catch(Restart&){}
    require(p.restarts==1 && !s_bootCache.safeResume && outputs.empty(),"off restart armed outputs");
  }else if(test=="software_resume_checked"){
    p._state=State::AwaitingStart;p._confirmedPhases=0;
    s_bootCache={};s_bootCache.magic=BOOT_CACHE_MAGIC;s_bootCache.desiredPhases=3;s_bootCache.safeResume=true;
    s_bootCache.crc=bootCacheCrc(s_bootCache);p.loadBootCache();
    require(p._state==State::WaitingForOff && p.reportedPhases()==0,"unconfirmed boot reported phases");
  }else if(test=="brownout_hold_loop" || test=="watchdog_hold_loop" || test=="panic_hold_loop"){
    pins[PIN_1P_IN]=0;p._state=State::AwaitingStart;p._confirmedPhases=0;
    s_bootCache={};s_bootCache.magic=BOOT_CACHE_MAGIC;s_bootCache.desiredPhases=3;s_bootCache.safeResume=true;
    s_bootCache.crc=bootCacheCrc(s_bootCache);
    resetReason=test=="brownout_hold_loop" ? ESP_RST_BROWNOUT : test=="panic_hold_loop" ? ESP_RST_PANIC : ESP_RST_TASK_WDT;
    p.loadBootCache();p.loop();clockMs+=20000;p.loop();
    require(p._state==State::AwaitingStart && p.reportedPhases()==0 && outputs.empty(),"unexpected reset energized contactor");
  }else if(test=="debounce_zero" || test=="debounce_wrap" || test=="debounce_interrupted"){
    FeedbackDebounce timer;clockMs=test=="debounce_wrap" ? UINT32_MAX-50 : 0;
    require(!persistsFor(true,timer,100),"premature first sample");
    clockMs+=99;require(!persistsFor(true,timer,100),"debounce shorter than 100 ms");
    if(test=="debounce_interrupted"){
      require(!persistsFor(false,timer,100),"false condition accepted");
      require(!persistsFor(true,timer,100),"interruption did not reset timer");clockMs+=99;
      require(!persistsFor(true,timer,100),"interrupted timer too short");
    }
    clockMs+=1;require(persistsFor(true,timer,100),"debounce did not expire");
  }else if(test=="feedback_reappears_off"){
    pins[PIN_1P_IN]=0;p._state=State::WaitingForOff;p.loop();clockMs+=100;p.loop();
    pins[PIN_1P_IN]=1;clockMs+=2000;p.loop();
    require(outputs.empty() && p._state==State::WaitingForOff,"energized despite feedback during deadtime");
  }else if(test=="manual_rearm"){
    pins[PIN_1P_IN]=0;p._state=State::AwaitingStart;p._confirmedPhases=0;
    require(!p.requestPhases(3),"Modbus bypassed manual reset acknowledgement");
    p.startSwitch(3,(uint8_t)SwitchSource::WebPage);p.loop();clockMs+=100;p.loop();clockMs+=2000;p.loop();
    require(outputs.size()==1 && outputs[0]==std::make_pair(PIN_3P_OUT,1) && !p._phaseSwitchRequested,"manual start failed or restored current");
  }else if(test=="phase_only_after_confirmation"){
    pins[PIN_1P_IN]=0;pins[PIN_3P_IN]=1;p._desiredPhases=3;p._confirmedPhases=0;p._state=State::SwitchedOn;
    require(p.reportedPhases()==0,"reported target before confirmation");p.loop();
    require(p.reportedPhases()==3 && p._state==State::Delay,"confirmed phases unavailable");
  }else if(test=="ota_active_guard"){
    require(!p.prepareUpdate() && outputs.empty() && p.restarts==0,"OTA accepted active current");
    p.sample={10,0,0,0};require(p.prepareUpdate(),"OTA rejected confirmed stop");
    require(!p.requestPhases(3),"phase switch during OTA accepted");
    require(p._updateInProgress && outputs.empty(),"OTA changed outputs");
  }else if(test=="ota_lock_error"){
    p.lockWriteOK=false;p.sample={10,0,0,0};
    require(!p.prepareUpdate() && p.restarts==0 && outputs.empty(),"OTA ignored lock write failure");
  }else if(test=="web_restart_active" || test=="web_timer_create_error" || test=="web_timer_start_error"){
    s_phase_switch=&p;timerCreateOK=test!="web_timer_create_error";timerStartOK=test!="web_timer_start_error";
    const bool scheduled=scheduleRestart(250000);
    if(!scheduled)p.requestRestart();else timerCallback(nullptr);
    p.loop();clockMs+=20001;p.loop();
    require(p.restarts==0 && outputs.empty(),"web restart bypassed stop guard");
    require(scheduled==(timerCreateOK&&timerStartOK),"timer error ignored");
  }else if(test=="web_null_controller"){
    require(!scheduleRestart(250000),"restart scheduled without controller");
  }else if(test=="unlock_invalid_status"){
    p._state=State::Unlocking;p.sample[0]=0xffff;p._unlockDeadlineMs=clockMs-1;p.loop();
    require(p._state==State::Unlocking,"invalid status accepted as unlock");
  }else{return 2;}
  return 0;
  }catch(const std::exception& error){std::cerr<<error.what()<<"\n"; return 1;}
}
'''


class SafetySimulation(unittest.TestCase):
    binary = None

    def setUp(self):
        name = self._testMethodName.removeprefix("test_")
        self.result = subprocess.run([self.binary, name], capture_output=True, text=True, timeout=5)
        # Infrastructure failures and requirement violations both fail the suite.
        self.assertIn(self.result.returncode, (0, 1), self.result.stderr)
        self.assertEqual(self.result.returncode, 0, self.result.stderr)

    def run_case(self, name):
        self.assertEqual(self.result.returncode, 0, f"{name}: {self.result.stderr}")


REGRESSION = ["restart_waits_for_zero", "restart_confirmed", "debounce_normal",
        "missing_feedback", "both_feedback", "fault_latched", "stop_write_error",
        "nonzero_current", "zero_read_error", "zero_confirmed", "stuck_contactor",
        "off_deadtime", "boot_current_zero", "switch_old_current_capped",
        "voltage_rejects_mixed", "watchdog_zero", "watchdog_nonzero",
        "software_cache", "brownout_cache", "panic_cache"]
FIXED_REGRESSIONS = ["restart_timeout", "restart_read_error", "restart_lock_error",
              "debounce_even", "wrong_feedback", "boot_without_request",
              "unlock_requires_confirmation", "phase_report_is_confirmation"]



def test_case(name):
    def test(self):
        self.run_case(name)
    test.__name__ = "test_" + name
    return test


for case in dict.fromkeys(REGRESSION + FIXED_REGRESSIONS + re.findall(r'test=="([^"]+)"', CASES)):
    method = test_case(case)
    setattr(SafetySimulation, "test_" + case, method)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--firmware-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    raw = (args.firmware_root / "src/switch.cpp").read_bytes()
    source = raw.decode()
    print(f"Candidate firmware SHA256 {hashlib.sha256(raw).hexdigest()}", flush=True)
    pages = (args.firmware_root / "src/pages.cpp").read_text()
    if "esp_restart(" in pages or "RESTART_FALLBACK" in pages:
        raise ValueError("web restart bypass reintroduced")
    if pages.index("if (!stopped)") > pages.index("if (esp_ota_begin("):
        raise ValueError("OTA erase occurs before stop confirmation")
    constants = source[source.index("static constexpr uint32_t FEEDBACK_DEBOUNCE_MS"):
                       source.index("// Register cache kept across")]
    boot_cache = source[source.index("static constexpr uint32_t BOOT_CACHE_MAGIC"):
                        source.index("struct TcpClientContext")]
    functions = ["msSince", "pastDeadline", "PhaseSwitch::loop",
                 "PhaseSwitch::handleRestart", "PhaseSwitch::writeRestartStop",
                 "PhaseSwitch::enterSafetyFault", "PhaseSwitch::hasSafetyFault",
                 "PhaseSwitch::reportedPhases", "PhaseSwitch::readActivePhases",
                 "PhaseSwitch::checkEvccWatchdog", "PhaseSwitch::loadBootCache",
                 "PhaseSwitch::saveBootCache", "PhaseSwitch::feedbackMatches",
                 "PhaseSwitch::outputsConfirmedOff", "PhaseSwitch::prepareUpdate",
                 "PhaseSwitch::setUpdateInProgress", "PhaseSwitch::canSwitchTo1P",
                 "PhaseSwitch::canSwitchTo3P", "PhaseSwitch::startSwitch",
                 "PhaseSwitch::requestPhases", "PhaseSwitch::requestRestart"]
    # The fixture defaults used in the boot tests are checked against the constructor.
    for initializer in ("_state(State::AwaitingStart)", "_desiredPhases(1)", "_confirmedPhases(0)", "_phaseSwitchRequested(false)"):
        if initializer not in source:
            raise ValueError("constructor default changed: " + initializer)
    with tempfile.TemporaryDirectory(prefix="heidelberg-safety-") as folder:
        path = Path(folder)
        cpp = path / "simulation.cpp"
        cpp.write_text(PRELUDE + constants + boot_cache + "\n".join(extract(source, f) for f in functions) + extract(pages, "scheduleRestart") + CASES)
        SafetySimulation.binary = str(path / "simulation")
        subprocess.run(["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-Wno-unused-parameter",
                        str(cpp), "-o", SafetySimulation.binary], check=True)
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(SafetySimulation)
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        print("All cases are hard regressions. Hardware safety remains unverified.")
        return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
