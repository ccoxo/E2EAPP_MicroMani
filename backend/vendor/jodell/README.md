# jodell gripper runtime

Place `jodellTool.dll` here on the deployment machine, or set:

```json
{
  "gripper": {
    "jodellDllPath": "C:/path/to/jodellTool.dll"
  }
}
```

The EPG006 gripper driver uses the same project API as `APP-Station-Docs`:

- `serialOperation(portNo, 115200, true/false)`
- `clawEnable(slaveId, true/false)`
- `runWithParam(slaveId, pos, speed, torque)`
- `clawEncoderZero(slaveId)`

Reference defaults from the upstream Qt project:

- left port: `COM8`
- right port: `COM9`
- left/right slave id: `9`
- fast open: `runWithParam(slave, 0, 255, 255)`
- fast close: `runWithParam(slave, 255, 255, 255)`
- soft grip: `runWithParam(slave, 238, 10, 50)`
- release: `runWithParam(slave, 210, 10, 10)`
