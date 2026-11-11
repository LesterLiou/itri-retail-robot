#include <cstdlib>
#include <cmath>
#include <algorithm>
#include <memory>
#include <QJsonDocument>
#include <QJsonObject>
#include <QLabel>
#include <QGridLayout>
#include <QPlainTextEdit>
#include <QProcess>
#include <QPushButton>
#include <QTimer>
#include <QVBoxLayout>
#include <rclcpp/rclcpp.hpp>
#include <rviz_common/panel.hpp>
#include <rviz_common/display_context.hpp>
#include <rviz_common/tool_manager.hpp>
#include <rviz_common/tool.hpp>
#include <rviz_common/view_manager.hpp>
#include <rviz_common/view_controller.hpp>
#include <rviz_common/properties/property.hpp>
#include <std_msgs/msg/string.hpp>
#include <nav_msgs/msg/occupancy_grid.hpp>
#include <std_srvs/srv/trigger.hpp>
#include <retail_interfaces/msg/gate_state.hpp>
#include <retail_interfaces/msg/risk_advisory.hpp>
#include <pluginlib/class_list_macros.hpp>

namespace retail_rviz_plugins {
class TaskControls : public rviz_common::Panel {
  Q_OBJECT
public:
  explicit TaskControls(QWidget * parent=nullptr) : Panel(parent) {
    const char * mode=std::getenv("CONTROL_MODE");
    manual_=mode && std::string(mode)=="moveit";
    auto layout=new QVBoxLayout(this);
    auto title=new QLabel(manual_?"MODE: MoveIt manual / FAKE hardware":"MODE: Nav2 mission / FAKE base",this);
    title->setWordWrap(true);layout->addWidget(title);
    auto execution_hint=new QLabel(manual_?"Execute requires a fresh Arm window below.":"MoveIt is PREVIEW ONLY here. For arm execution: host make moveit.",this);
    execution_hint->setWordWrap(true);execution_hint->setStyleSheet("color:#a43819;font-weight:bold");layout->addWidget(execution_hint);
    state_=new QLabel("Waiting for ROS feedback",this);state_->setWordWrap(true);layout->addWidget(state_);
    risk_=new QLabel("VQA: waiting",this);risk_->setWordWrap(true);layout->addWidget(risk_);
    auto buttons=new QGridLayout();layout->addLayout(buttons);
    auto add=[&](const QString & text,int row,int column,int span=1){auto button=new QPushButton(text,this);buttons->addWidget(button,row,column,1,span);return button;};
    auto top=add("View: Navigation (top)",0,0);
    auto orbit=add("View: Arm (3D)",0,1);
    connect(top,&QPushButton::clicked,this,[this](){setView(true);});
    connect(orbit,&QPushButton::clicked,this,[this](){setView(false);});
    goal_=add("Nav2: Set goal on map",1,0,2);
    reset_=add("Nav2: Reset HOLD (no motion)",2,0);
    cancel_=add("Nav2: Cancel task / HOLD",2,1);
    arm_=add("MoveIt: Arm execution window (15 s)",3,0);
    end_=add("MoveIt: End window / stop owned motion",3,1);
    auto help=new QLabel("Nav2: Set goal, then drag on map.\nMoveIt: Joints → Arm window → Plan & Execute.\nMode: host make nav-vqa / make moveit. Preview works in both.",this);
    help->setWordWrap(true);layout->addWidget(help);
    log_=new QPlainTextEdit(this);log_->setReadOnly(true);log_->setMaximumBlockCount(100);log_->setMaximumHeight(90);layout->addWidget(log_);
    process_=new QProcess(this);process_->setProcessChannelMode(QProcess::MergedChannels);
    connect(process_,&QProcess::readyReadStandardOutput,this,[this](){log_->appendPlainText(QString::fromUtf8(process_->readAllStandardOutput()));});
    connect(process_,&QProcess::errorOccurred,this,[this](QProcess::ProcessError){log_->appendPlainText(process_->errorString());});
    connect(arm_,&QPushButton::clicked,this,[this](){
      if(manual_ && process_->state()==QProcess::NotRunning)
        // Own the actual lease process, not a ros2-run wrapper: SIGTERM must
        // reach manual_control's verified stop/lease cleanup handler directly.
        process_->start("python3",QStringList{"-u","-c","from retail_core.manual_control import main; raise SystemExit(main())","prepare"});
    });
    connect(end_,&QPushButton::clicked,this,[this](){if(process_->state()!=QProcess::NotRunning)process_->terminate();});
    connect(goal_,&QPushButton::clicked,this,[this](){
      if(manual_ || !getDisplayContext())return;
      auto manager=getDisplayContext()->getToolManager();rviz_common::Tool * goal=nullptr;
      for(int i=0;i<manager->numTools();++i){
        auto tool=manager->getTool(i);
        if(tool->getPropertyContainer()->subProp("Topic")->getValue().toString()=="/mission/goal_pose"){goal=tool;break;}
      }
      if(!goal){goal=manager->addTool("rviz_default_plugins/SetGoal");goal->getPropertyContainer()->subProp("Topic")->setValue("/mission/goal_pose");}
      manager->setCurrentTool(goal);log_->appendPlainText("Drag a NEW goal on the map. Active/held/conflicting goals are rejected, never queued.");
    });
    goal_->setEnabled(false);reset_->setEnabled(false);cancel_->setEnabled(false);arm_->setEnabled(false);end_->setEnabled(false);
    timer_=new QTimer(this);connect(timer_,&QTimer::timeout,this,[this](){refresh();});
  }
  void onInitialize() override {
    node_=std::make_shared<rclcpp::Node>("retail_rviz_controls");
    executor_=std::make_unique<rclcpp::executors::SingleThreadedExecutor>();executor_->add_node(node_);
    reset_client_=node_->create_client<std_srvs::srv::Trigger>("/mission/reset");
    cancel_client_=node_->create_client<std_srvs::srv::Trigger>("/mission/cancel");
    connect(reset_,&QPushButton::clicked,this,[this](){call(reset_client_);});
    connect(cancel_,&QPushButton::clicked,this,[this](){call(cancel_client_);});
    mission_sub_=node_->create_subscription<std_msgs::msg::String>("/mission/state",10,[this](std_msgs::msg::String::ConstSharedPtr msg){
      mission_=QJsonDocument::fromJson(QByteArray::fromStdString(msg->data)).object();mission_at_=node_->now().seconds();
    });
    gate_sub_=node_->create_subscription<retail_interfaces::msg::GateState>("/base/gate_state",10,[this](retail_interfaces::msg::GateState::ConstSharedPtr msg){gate_=msg;});
    risk_sub_=node_->create_subscription<retail_interfaces::msg::RiskAdvisory>("/perception/vqa/chest",10,[this](retail_interfaces::msg::RiskAdvisory::ConstSharedPtr msg){risk_msg_=msg;});
    map_sub_=node_->create_subscription<nav_msgs::msg::OccupancyGrid>("/map",rclcpp::QoS(1).transient_local(),[this](nav_msgs::msg::OccupancyGrid::ConstSharedPtr msg){
      const auto &info=msg->info;const auto &q=info.origin.orientation;
      const double yaw=std::atan2(2*(q.w*q.z+q.x*q.y),1-2*(q.y*q.y+q.z*q.z));
      const double x=info.width*info.resolution/2,y=info.height*info.resolution/2;
      map_x_=info.origin.position.x+std::cos(yaw)*x-std::sin(yaw)*y;
      map_y_=info.origin.position.y+std::sin(yaw)*x+std::cos(yaw)*y;
    });
    timer_->start(100);
  }
  ~TaskControls() override {
    timer_->stop();
    if(process_->state()!=QProcess::NotRunning){process_->terminate();if(!process_->waitForFinished(3000)){process_->kill();process_->waitForFinished(1000);}}
  }
private:
  using Trigger=std_srvs::srv::Trigger;
  void setView(bool top){
    if(!getDisplayContext())return;
    auto manager=getDisplayContext()->getViewManager();
    manager->setCurrentViewControllerType(top?"rviz_default_plugins/TopDownOrtho":"rviz_default_plugins/Orbit");
    auto view=manager->getCurrent();
    view->subProp("Target Frame")->setValue(top?"map":"base_link");
    if(top){view->subProp("Scale")->setValue(90.0);view->subProp("Angle")->setValue(0.0);view->subProp("X")->setValue(map_x_);view->subProp("Y")->setValue(map_y_);}
    else{
      view->subProp("Distance")->setValue(3.0);view->subProp("Pitch")->setValue(.45);view->subProp("Yaw")->setValue(.7);
      view->subProp("Focal Point")->subProp("X")->setValue(0.0);
      view->subProp("Focal Point")->subProp("Y")->setValue(0.0);
      view->subProp("Focal Point")->subProp("Z")->setValue(.9);
    }
  }
  void call(rclcpp::Client<Trigger>::SharedPtr client){
    if(!client->service_is_ready()){log_->appendPlainText("Service unavailable; check selected mode and ROS launch.");return;}
    client->async_send_request(std::make_shared<Trigger::Request>(),[this](rclcpp::Client<Trigger>::SharedFuture future){
      const auto response=future.get();log_->appendPlainText((response->success?"Request accepted: ":"Request refused: ")+QString::fromStdString(response->message));
    });
  }
  void refresh(){
    if(!executor_ || !rclcpp::ok())return;
    executor_->spin_some();const double now=node_->now().seconds();
    const bool fresh=gate_ && now-rclcpp::Time(gate_->header.stamp).seconds()>=0 && now-rclcpp::Time(gate_->header.stamp).seconds()<.5;
    const bool task_fresh=now-mission_at_<1.0;
    const auto phase=task_fresh?mission_["phase"].toString():QString("UNKNOWN");
    const auto permission=fresh?(gate_->hold?"HOLD":(gate_->enabled?"ENABLED":"DISABLED")):"UNKNOWN";
    state_->setText("Task: "+(manual_?QString("manual"):phase)+" / Gate: "+permission+"\n"+(task_fresh?mission_["reason"].toString():QString())+"\n"+(fresh?QString::fromStdString(gate_->reason):QString("Gate feedback stale/unavailable")));
    if(risk_msg_){const auto age=now-rclcpp::Time(risk_msg_->header.stamp).seconds();const auto ttl=risk_msg_->valid_for.sec+risk_msg_->valid_for.nanosec/1e9;const bool valid=risk_msg_->valid && age>=0 && age<=std::min(2.0,ttl);
      risk_->setText("VQA: "+QString::fromStdString(valid?risk_msg_->action:"DATA_LOSS")+" / age "+QString::number(age,'f',2)+" s\n"+QString::fromStdString(risk_msg_->reason));}
    goal_->setEnabled(!manual_ && fresh && task_fresh && !gate_->hold && (phase=="IDLE" || phase=="DONE"));
    reset_->setEnabled(!manual_ && fresh && task_fresh && gate_->base_standstill && gate_->arms_stowed && (phase=="IDLE" || phase=="DONE" || phase=="HOLD" || phase=="FAULT"));
    cancel_->setEnabled(!manual_ && cancel_client_->service_is_ready());
    arm_->setEnabled(manual_ && fresh && gate_->base_standstill && !gate_->hold && process_->state()==QProcess::NotRunning);
    end_->setEnabled(manual_ && process_->state()!=QProcess::NotRunning);
  }
  bool manual_=false;double mission_at_=-1e9,map_x_=1.0,map_y_=0.0;QJsonObject mission_;
  QLabel *state_,*risk_;QPushButton *goal_,*reset_,*cancel_,*arm_,*end_;QPlainTextEdit *log_;QProcess *process_;QTimer *timer_;
  rclcpp::Node::SharedPtr node_;std::unique_ptr<rclcpp::executors::SingleThreadedExecutor> executor_;
  rclcpp::Client<Trigger>::SharedPtr reset_client_,cancel_client_;
  rclcpp::Subscription<std_msgs::msg::String>::SharedPtr mission_sub_;
  rclcpp::Subscription<nav_msgs::msg::OccupancyGrid>::SharedPtr map_sub_;
  rclcpp::Subscription<retail_interfaces::msg::GateState>::SharedPtr gate_sub_;
  rclcpp::Subscription<retail_interfaces::msg::RiskAdvisory>::SharedPtr risk_sub_;
  retail_interfaces::msg::GateState::ConstSharedPtr gate_;
  retail_interfaces::msg::RiskAdvisory::ConstSharedPtr risk_msg_;
};
}
PLUGINLIB_EXPORT_CLASS(retail_rviz_plugins::TaskControls,rviz_common::Panel)
#include "controls_panel.moc"
