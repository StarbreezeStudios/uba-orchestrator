using UnrealBuildTool;

[SupportedPlatforms(UnrealPlatformClass.Desktop)]
public class UbaCoordinatorOrchestrator : ModuleRules
{
    public UbaCoordinatorOrchestrator(ReadOnlyTargetRules Target) : base(Target)
    {
        PrivatePCHHeaderFile = "../Core/Public/UbaCorePch.h";
        PrivateDependencyModuleNames.Add("UbaCommon");
        if (Target.Platform == UnrealTargetPlatform.Win64)
            PublicSystemLibraries.Add("Winhttp.lib");
    }
}
