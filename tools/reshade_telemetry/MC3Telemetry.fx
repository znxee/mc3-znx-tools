texture2D MC3BackBufferTex : COLOR;
sampler2D MC3BackBuffer
{
    Texture = MC3BackBufferTex;
};

uniform float MC3Connected < source = "mc3_connected"; > = 0.0;
uniform float MC3SpeedKmh < source = "mc3_speed_kmh"; > = 0.0;
uniform float MC3RPM < source = "mc3_rpm"; > = 0.0;
uniform int MC3Gear < source = "mc3_gear"; > = 0;
uniform uint MC3Flags < source = "mc3_flags"; > = 0;
uniform float3 MC3Velocity < source = "mc3_velocity"; > = float3(0.0, 0.0, 0.0);

void VS_MC3Telemetry(uint vertex_id : SV_VertexID,
                     out float4 position : SV_Position,
                     out float2 texcoord : TEXCOORD)
{
    texcoord.x = (vertex_id == 2) ? 2.0 : 0.0;
    texcoord.y = (vertex_id == 1) ? 2.0 : 0.0;
    position = float4(texcoord * float2(2.0, -2.0) + float2(-1.0, 1.0),
                      0.0, 1.0);
}

float4 PS_MC3TelemetryTest(float4 position : SV_Position,
                           float2 texcoord : TEXCOORD) : SV_Target
{
    float4 color = tex2D(MC3BackBuffer, texcoord);
    if (MC3Connected < 0.5)
        return color;

    const float speed = saturate(MC3SpeedKmh / 300.0);
    const float rpm = saturate(MC3RPM / 9000.0);

    // Thin proof-of-life bar: green means connected, its length follows speed
    // and its red component follows RPM. The rest is a subtle speed vignette.
    const float bar_width = max(0.012, speed);
    if (texcoord.y < 0.008 && texcoord.x < bar_width)
        color.rgb = lerp(color.rgb, float3(rpm, 1.0 - rpm * 0.35, 0.15), 0.9);

    const float edge = smoothstep(0.48, 0.72, length(texcoord - 0.5));
    color.rgb += edge * speed * float3(0.012, 0.020, 0.050);
    return color;
}

technique MC3TelemetryTest < ui_label = "MC3 Telemetry Test"; >
{
    pass
    {
        VertexShader = VS_MC3Telemetry;
        PixelShader = PS_MC3TelemetryTest;
    }
}
