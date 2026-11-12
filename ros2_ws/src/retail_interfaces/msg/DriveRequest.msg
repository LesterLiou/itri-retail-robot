# The TidyBot boot ID and task ID bind requests to one local motion session.
uint8 MAP=1
uint8 STRAIGHT=2
uint8 START=1
uint8 HEARTBEAT=2
uint8 VELOCITY=3
uint8 FINISH=4
uint8 CANCEL=5
std_msgs/Header header
string boot_id
string task_id
uint64 sequence
uint8 mode
uint8 operation
geometry_msgs/Twist velocity
