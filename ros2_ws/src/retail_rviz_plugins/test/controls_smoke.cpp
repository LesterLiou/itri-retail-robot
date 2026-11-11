// Isolated ROS graph only: mock Trigger services, no robot or velocity endpoints.
#include <chrono>
#include <cstdlib>
#include <iostream>
#include <stdexcept>
#include <thread>
#include <QApplication>
#include <QPushButton>
#include <QPlainTextEdit>
#include <pluginlib/class_loader.hpp>
#include <rviz_common/panel.hpp>
#include <rclcpp/rclcpp.hpp>
#include <retail_interfaces/msg/gate_state.hpp>
#include <std_msgs/msg/string.hpp>
#include <std_srvs/srv/trigger.hpp>
using namespace std::chrono_literals;
int main(int argc,char **argv){
  QApplication app(argc,argv);rclcpp::init(argc,argv);
  auto node=std::make_shared<rclcpp::Node>("mock_ui_services");
  if(argc>1 && std::string(argv[1])=="--manual-backend"){
    // Invoked only by accept_rviz_manual.bash in an isolated GenericSystem graph.
    const char *mode=std::getenv("CONTROL_MODE");if(!mode || std::string(mode)!="moveit")return 2;
    pluginlib::ClassLoader<rviz_common::Panel> loader("rviz_common","rviz_common::Panel");
    auto panel=loader.createSharedInstance("retail_rviz_plugins/TaskControls");panel->onInitialize();
    auto find=[&](const QString & prefix){for(auto b:panel->findChildren<QPushButton*>())if(b->text().startsWith(prefix))return b;throw std::runtime_error("missing button");};
    auto log=panel->findChild<QPlainTextEdit*>();
    retail_interfaces::msg::GateState::ConstSharedPtr feedback;
    auto sub=node->create_subscription<retail_interfaces::msg::GateState>("/base/gate_state",10,[&](retail_interfaces::msg::GateState::ConstSharedPtr msg){feedback=msg;});
    auto until=[&](auto ready){const auto end=std::chrono::steady_clock::now()+15s;while(std::chrono::steady_clock::now()<end){rclcpp::spin_some(node);app.processEvents();if(ready())return;std::this_thread::sleep_for(20ms);}throw std::runtime_error("manual panel backend timeout: "+log->toPlainText().toStdString());};
    try{
      until([&](){return find("MoveIt: Arm")->isEnabled();});
      find("MoveIt: Arm")->click();
      until([&](){return log->toPlainText().contains("GUI execution window:");});
      until([&](){return find("MoveIt: End")->isEnabled();});
      find("MoveIt: End")->click();
      until([&](){return log->toPlainText().contains("Stopped: measured standstill") && feedback && feedback->hold && feedback->base_standstill && !feedback->enabled;});
      std::cout<<"PASS: actual Qt Arm/End buttons launched the existing manual lease owner, opened a fresh window, cancelled it, and observed stopped FAKE feedback / HOLD"<<std::endl;
      panel.reset();rclcpp::shutdown();return 0;
    }catch(const std::exception &e){std::cerr<<e.what()<<std::endl;panel.reset();rclcpp::shutdown();return 1;}
  }
  int resets=0,cancels=0;
  using Trigger=std_srvs::srv::Trigger;
  auto reset=node->create_service<Trigger>("/mission/reset",[&](Trigger::Request::SharedPtr,Trigger::Response::SharedPtr response){++resets;response->success=true;response->message="mock reset";});
  auto cancel=node->create_service<Trigger>("/mission/cancel",[&](Trigger::Request::SharedPtr,Trigger::Response::SharedPtr response){++cancels;response->success=true;response->message="mock cancel";});
  auto gate=node->create_publisher<retail_interfaces::msg::GateState>("/base/gate_state",10);
  auto mission=node->create_publisher<std_msgs::msg::String>("/mission/state",10);
  pluginlib::ClassLoader<rviz_common::Panel> loader("rviz_common","rviz_common::Panel");
  auto button=[](rviz_common::Panel * panel,const QString & prefix){
    for(auto candidate:panel->findChildren<QPushButton*>())if(candidate->text().startsWith(prefix))return candidate;
    throw std::runtime_error("missing button");
  };
  bool publish=true,hold=false;
  auto pump=[&](int ms){
    const auto until=std::chrono::steady_clock::now()+std::chrono::milliseconds(ms);
    while(std::chrono::steady_clock::now()<until){
      if(publish){retail_interfaces::msg::GateState state;state.header.stamp=node->now();state.hold=hold;state.base_standstill=true;state.arms_stowed=true;gate->publish(state);
        std_msgs::msg::String msg;msg.data="{\"phase\":\"DONE\",\"reason\":\"mock only\"}";mission->publish(msg);}
      rclcpp::spin_some(node);app.processEvents();std::this_thread::sleep_for(20ms);
    }
  };
  auto require=[](bool ok,const char *reason){if(!ok)throw std::runtime_error(reason);};
  try{
    setenv("CONTROL_MODE","mission",1);
    {auto panel=loader.createSharedInstance("retail_rviz_plugins/TaskControls");panel->onInitialize();pump(2000);
      require(button(panel.get(),"Nav2: Set")->isEnabled(),"healthy idle task must allow goal tool");
      require(!button(panel.get(),"MoveIt: Arm")->isEnabled(),"mission must reject arm window");
      button(panel.get(),"Nav2: Reset")->click();button(panel.get(),"Nav2: Cancel")->click();pump(500);
      require(resets==1 && cancels==1,"buttons must use task-owned Trigger services exactly once");
      hold=true;pump(300);require(!button(panel.get(),"Nav2: Set")->isEnabled(),"HOLD must disable new goal button");
      publish=false;pump(700);require(!button(panel.get(),"Nav2: Reset")->isEnabled(),"stale stop feedback must disable reset");
    }
    setenv("CONTROL_MODE","moveit",1);hold=false;publish=true;
    {auto panel=loader.createSharedInstance("retail_rviz_plugins/TaskControls");panel->onInitialize();pump(1500);
      require(!button(panel.get(),"Nav2: Set")->isEnabled(),"manual mode must reject navigation tool");
      require(button(panel.get(),"MoveIt: Arm")->isEnabled(),"stopped fake manual mode may request backend lease window");
      hold=true;pump(300);require(!button(panel.get(),"MoveIt: Arm")->isEnabled(),"HOLD must disable manual window");
    }
    std::cout<<"PASS: installed RViz plugin, typed reset/cancel callbacks, mode isolation, HOLD and stale-feedback UI cases; no motion endpoints"<<std::endl;
  }catch(const std::exception &e){std::cerr<<e.what()<<std::endl;rclcpp::shutdown();return 1;}
  rclcpp::shutdown();return 0;
}
